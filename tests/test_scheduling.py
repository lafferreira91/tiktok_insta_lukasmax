"""The slot planner: no collisions, no rut, reproducible.

Two posts a day for weeks is where a naive planner shows its flaws -- every day
lands on the same hour, or two posts collide, or a re-run produces a different
calendar than the dry run promised.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from lukasmax_automation import scheduling
from lukasmax_automation.scheduling import DEFAULT_CONFIG, plan_slots, tune_weights

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def _proxima_segunda() -> date:
    """A proxima segunda-feira ainda no futuro.

    Era 10/08/2026, fixa, e o teste apodreceu sozinho quando essa data virou
    passado: ``plan_slots`` recusa horario anterior a agora, entao o
    planejamento voltava vazio sem nada no codigo ter mudado.
    """
    hoje = date.today()
    return hoje + timedelta(days=(7 - hoje.weekday()) % 7 or 7)


MONDAY = _proxima_segunda()


def config(**overrides):
    merged = {**DEFAULT_CONFIG, **overrides}
    return merged


class TestNoCollisions:
    def test_two_posts_a_day_for_a_month_never_share_a_minute(self):
        planned = plan_slots(MONDAY, 30, config(), per_day=2)

        stamps = [slot.local for slot in planned]
        assert len(stamps) == len(set(stamps)), "dois posts no mesmo instante"

    def test_same_day_posts_respect_the_minimum_gap(self):
        planned = plan_slots(MONDAY, 14, config(), per_day=2)
        gap = timedelta(minutes=DEFAULT_CONFIG["min_gap_minutes"])

        by_day: dict[date, list[datetime]] = {}
        for slot in planned:
            by_day.setdefault(slot.local.date(), []).append(slot.local)

        for day, times in by_day.items():
            times.sort()
            for earlier, later in zip(times, times[1:], strict=False):
                assert later - earlier >= gap, f"{day}: posts a menos de {gap} de distancia"

    def test_already_occupied_times_are_avoided(self):
        """The pilot is already on the calendar; nothing may land on top of it."""
        taken = datetime(2026, 8, 12, 18, 0, tzinfo=SAO_PAULO)
        planned = plan_slots(MONDAY, 14, config(), occupied=[taken], per_day=2)

        gap = timedelta(minutes=DEFAULT_CONFIG["min_gap_minutes"])
        for slot in planned:
            assert abs(slot.local - taken) >= gap, f"{slot.local} colide com o item existente"


class TestNotBefore:
    """Planning a window that starts today must not schedule into the past."""

    def test_slots_earlier_today_are_skipped(self):
        afternoon = datetime(2026, 8, 10, 16, 0, tzinfo=SAO_PAULO)

        planned = plan_slots(MONDAY, 3, config(), per_day=2, not_before=afternoon)

        assert planned, "o planejamento nao pode ficar vazio por causa do corte"
        for slot in planned:
            assert slot.local > afternoon, f"{slot.local} ja tinha passado"

    def test_a_late_start_pushes_the_first_post_to_the_next_day(self):
        # Derivado de MONDAY: uma data fixa aqui deixa de ser 'tarde no
        # primeiro dia' assim que MONDAY passa dela.
        late = datetime.combine(MONDAY, time(23, 59), tzinfo=SAO_PAULO)

        planned = plan_slots(MONDAY, 3, config(), per_day=2, not_before=late)

        assert planned[0].local.date() > MONDAY

    def test_the_day_still_fills_when_the_cut_is_early(self):
        dawn = datetime(2026, 8, 10, 5, 0, tzinfo=SAO_PAULO)

        planned = plan_slots(MONDAY, 1, config(), per_day=2, not_before=dawn)

        assert len(planned) == 2, "um corte de madrugada nao pode custar slots do dia"


class TestNoRut:
    def test_does_not_post_at_the_same_hour_every_day(self):
        planned = plan_slots(MONDAY, 21, config(), per_day=2)

        hours = Counter(slot.local.hour for slot in planned)
        assert len(hours) >= 3, f"so {len(hours)} horario(s) distintos: {dict(hours)}"

    def test_the_top_slot_does_not_take_every_single_day(self):
        planned = plan_slots(MONDAY, 21, config(), per_day=1)

        used = Counter(slot.slot_id for slot in planned)
        most_common = used.most_common(1)[0][1]
        assert most_common < len(planned), "um unico slot levou todos os dias"

    def test_weekday_and_weekend_use_different_pools(self):
        planned = plan_slots(MONDAY, 14, config(), per_day=2)

        for slot in planned:
            is_weekend = slot.local.weekday() >= 5
            prefix = "we-" if is_weekend else "wd-"
            assert slot.slot_id.startswith(prefix), (
                f"{slot.slot_id} usado num {'fim de semana' if is_weekend else 'dia util'}"
            )


class TestDeterminism:
    def test_replanning_the_same_window_gives_the_same_calendar(self):
        """A dry run has to match what actually gets written."""
        first = plan_slots(MONDAY, 14, config(), per_day=2)
        second = plan_slots(MONDAY, 14, config(), per_day=2)

        assert [slot.scheduled_at for slot in first] == [slot.scheduled_at for slot in second]

    def test_jitter_moves_posts_off_the_exact_minute(self):
        planned = plan_slots(MONDAY, 21, config(), per_day=2)

        minutes = {slot.local.minute for slot in planned}
        assert len(minutes) > 3, "todos os posts cairam nos mesmos minutos cravados"

    def test_jitter_stays_within_its_bounds(self):
        spread = DEFAULT_CONFIG["jitter_minutes"]
        planned = plan_slots(MONDAY, 14, config(), per_day=2)
        pool = {slot["id"]: slot["time"] for slot in DEFAULT_CONFIG["pool"]}

        for slot in planned:
            hour, minute = (int(part) for part in pool[slot.slot_id].split(":"))
            base = slot.local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            assert abs((slot.local - base).total_seconds()) <= spread * 60


class TestTimezone:
    def test_local_times_carry_the_brazilian_offset(self):
        planned = plan_slots(MONDAY, 3, config(), per_day=2)

        for slot in planned:
            assert slot.scheduled_at.endswith("-03:00")

    def test_utc_mirror_matches_the_local_time(self):
        planned = plan_slots(MONDAY, 3, config(), per_day=2)

        for slot in planned:
            local = datetime.fromisoformat(slot.scheduled_at)
            utc = datetime.fromisoformat(slot.scheduled_at_utc)
            assert local == utc


class TestExploration:
    def test_least_sampled_slots_get_a_turn(self):
        """Without this the engine only ever learns about its starting slots."""
        pool = [dict(slot) for slot in DEFAULT_CONFIG["pool"]]
        for slot in pool:
            slot["samples"] = 100 if slot["id"] != "wd-morning" else 0
            slot["weight"] = 0.1 if slot["id"] == "wd-morning" else 1.0

        planned = plan_slots(MONDAY, 28, config(pool=pool, explore_every=5), per_day=2)

        used = {slot.slot_id for slot in planned}
        assert "wd-morning" in used, "o slot menos observado nunca foi testado"

    def test_exploration_can_be_switched_off(self):
        planned = plan_slots(MONDAY, 14, config(explore_every=0), per_day=2)
        assert planned, "desligar a exploracao nao pode zerar o planejamento"


# Os testes de tuning falam da matematica, nao do conteudo do pool. Nomear
# slots a mao os quebrou quando os horarios mudaram -- um falso alarme, ja que
# o encolhimento bayesiano nao sabe nada sobre que horas sao.
FORTE, FRACO = (slot["id"] for slot in DEFAULT_CONFIG["pool"][:2])
TERCEIRO = DEFAULT_CONFIG["pool"][2]["id"]


class TestTuning:
    def test_a_strong_slot_gains_weight_and_a_weak_one_loses(self):
        performance = {FORTE: [0.5] * 20, FRACO: [0.05] * 20}

        tuned = tune_weights(DEFAULT_CONFIG, performance)
        weights = {slot["id"]: slot["weight"] for slot in tuned["pool"]}

        assert weights[FORTE] > weights[FRACO]
        assert tuned["source"] == "data-driven"

    def test_shrinkage_keeps_a_single_sample_from_dominating(self):
        """One lucky post must not crown a slot.

        Both slots performed identically well; only the evidence differs. The
        comparison stays inside one scenario on purpose -- weights are relative
        to the global mean, so numbers from two different datasets say nothing.
        """
        tuned = tune_weights(
            DEFAULT_CONFIG,
            {
                FORTE: [1.0] * 50,  # muita evidencia
                FRACO: [1.0],  # um post de sorte
                TERCEIRO: [0.1] * 50,  # puxa a media global para baixo
            },
        )
        weights = {slot["id"]: slot["weight"] for slot in tuned["pool"]}

        assert weights[FRACO] < weights[FORTE], "um unico post rendeu o mesmo peso que cinquenta"

    def test_unobserved_slots_keep_their_prior_and_survive(self):
        tuned = tune_weights(DEFAULT_CONFIG, {FORTE: [0.4] * 10})

        antes = {slot["id"]: slot["weight"] for slot in DEFAULT_CONFIG["pool"]}
        sem_dados = next(slot for slot in tuned["pool"] if slot["id"] == FRACO)
        assert sem_dados["samples"] == 0
        assert sem_dados["weight"] == pytest.approx(antes[FRACO]), (
            "um slot sem dados nao pode ser punido"
        )
        assert len(tuned["pool"]) == len(DEFAULT_CONFIG["pool"]), "um slot foi removido"

    def test_no_data_leaves_the_config_untouched(self):
        assert tune_weights(DEFAULT_CONFIG, {}) == DEFAULT_CONFIG


class TestConfig:
    def test_missing_file_falls_back_to_the_defaults(self, tmp_path):
        loaded = scheduling.load_slots(tmp_path / "ausente.json")
        assert len(loaded["pool"]) == len(DEFAULT_CONFIG["pool"])

    def test_saved_config_round_trips(self, tmp_path):
        path = tmp_path / "slots.json"
        scheduling.save_slots(DEFAULT_CONFIG, path)
        assert scheduling.load_slots(path) == DEFAULT_CONFIG

    def test_empty_pool_is_an_error_not_a_silent_no_op(self):
        with pytest.raises(scheduling.SchedulingError):
            plan_slots(MONDAY, 7, config(pool=[]), per_day=2)
