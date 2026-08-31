"""Caption generation: the cache, the validator, and the approval gate.

No test here calls the API. Generation is exercised through a fake client, so
the suite stays free and offline.
"""

from __future__ import annotations

import json

import pytest

from lukasmax_automation import captions as captions_mod
from lukasmax_automation.captions import CaptionError, approve, draft, fingerprint, validate


class FakeBlock:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class FakeResponse:
    stop_reason = "end_turn"

    def __init__(self, payload: dict):
        self.content = [FakeBlock(json.dumps(payload, ensure_ascii=False))]


class FakeClient:
    """Counts calls, so cache behaviour is observable."""

    def __init__(self, payload: dict | None = None, *, stop_reason: str = "end_turn"):
        self.payload = payload or {
            "caption": (
                "Essa musica desbloqueou uma memoria que eu nem sabia que tinha. "
                "Salva ai para ouvir de novo depois e manda para quem cantava com voce."
            ),
            "hashtags": ["#NostalgiaMusical", "#CarJams", "#MusicaBrasileira"],
            "alt_text": "Homem canta dentro do carro enquanto dirige.",
        }
        self.stop_reason = stop_reason
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        response = FakeResponse(self.payload)
        response.stop_reason = self.stop_reason
        return response


METADATA = {
    "id": "111",
    "title": "quando toca essa musica",
    "track": "Toda Toda",
    "artists": ["Pikeno & Menor"],
    "view_count": 3_100_000,
    "like_count": 353_500,
}


class TestValidator:
    def test_a_good_caption_has_no_warnings(self):
        caption = (
            "Essa desbloqueou uma memoria que eu nem sabia que tinha. "
            "Salva para ouvir depois e manda para quem cantava essa com voce."
        )
        assert validate(caption, ["#NostalgiaMusical", "#CarJams", "#Anos2010"]) == []

    def test_mentioning_the_source_platform_is_blocked(self):
        warnings = validate("Repostando do TikTok porque merece", ["#a", "#b", "#c"])
        assert any("tiktok" in warning for warning in warnings)

    def test_apontar_para_a_playlist_da_bio_e_permitido(self):
        """A bio TEM um link de playlist -- confirmado pelo dono em 31/08/2026.

        "link na bio" era proibido desde o inicio, com a justificativa de ser
        "peso morto numa conta sem link". A premissa era falsa, e por causa dela
        eu cheguei a barrar e reescrever uma legenda perfeitamente boa
        ("Playlist na bio"). O unico termo que sobra na lista e a plataforma de
        origem, que e proibicao de politica, nao de premissa minha.
        """
        assert validate("Playlist na bio", ["#a", "#b", "#c"]) == []

    def test_too_many_hashtags_hits_the_instagram_ceiling(self):
        caption = "Uma legenda perfeitamente adequada " * 3
        warnings = validate(caption, [f"#t{i}" for i in range(31)])
        assert any("limite do Instagram" in warning for warning in warnings)

    def test_hashtag_count_outside_the_target_range_warns(self):
        caption = "Uma legenda com tamanho adequado para o teste passar tranquilamente aqui."
        assert any("alvo" in warning for warning in validate(caption, ["#um"]))

    def test_a_caption_over_2200_characters_is_rejected(self):
        assert any("limite do Instagram" in warning for warning in validate("x" * 2300, ["#a"]))

    def test_uma_legenda_curtissima_e_valida_na_v2(self):
        """O piso de tamanho saiu junto com a segunda frase (31/08/2026).

        Ate a v1 isto era 'legenda muito curta', porque a forma esperada era um
        paragrafo. A v2 pede uma frase so, e "Trevo 🍀" -- 7 caracteres -- e o
        formato pedido, nao um defeito. 26 das 161 legendas agendadas ficam
        abaixo de 15 caracteres depois do corte.
        """
        assert validate("Trevo 🍀", ["#a", "#b", "#c"]) == []

    def test_legenda_vazia_continua_bloqueada(self):
        """O unico minimo que sobrou. Sem ele, remover o piso abriria um buraco."""
        assert any("vazia" in warning for warning in validate("   ", ["#a", "#b", "#c"]))

    def test_uma_segunda_frase_em_outra_linha_e_bloqueada(self):
        """A regra que define a v2: a segunda frase entrava como bloco novo."""
        caption = "Sete dias da semana 📅\n\nEle contou. Eu so dublei."
        assert any("uma linha" in warning for warning in validate(caption, ["#a", "#b", "#c"]))

    def test_a_legenda_nao_pode_passar_da_dobra_do_mais(self):
        """Na v2 o teto e o proprio gancho: a frase inteira tem de ser visivel."""
        caption = "A" * 130
        assert any("125" in warning for warning in validate(caption, ["#a", "#b", "#c"]))

    def test_a_hook_left_hanging_on_a_conjunction_warns(self):
        caption = "A" * 120 + " e o resto da frase continua bem depois do corte da dobra."
        assert any("gancho" in warning for warning in validate(caption, ["#a", "#b", "#c"]))

    def test_a_word_merely_ending_in_a_conjunction_letter_is_fine(self):
        """endswith('e') would flag 'esquece', 'importante' and 'onde'."""
        caption = "A" * 116 + " esquece o resto da frase que vem bem depois do corte."
        assert not any("gancho" in warning for warning in validate(caption, ["#a", "#b", "#c"]))

    def test_a_hook_cut_on_a_comma_warns(self):
        caption = "A" * 124 + ", e entao a frase continua bem depois da dobra do 'mais'."
        assert any("gancho" in warning for warning in validate(caption, ["#a", "#b", "#c"]))

    def test_hashtag_without_the_hash_is_caught(self):
        caption = "Uma legenda com tamanho adequado para o teste passar tranquilamente aqui."
        assert any("sem '#'" in warning for warning in validate(caption, ["#ok", "semhash", "#b"]))


class TestApprovalGate:
    def test_a_clean_caption_can_be_approved(self):
        record = {
            "tiktok_id": "111",
            "caption": (
                "Essa desbloqueou uma memoria que eu nem sabia que tinha. "
                "Salva para ouvir depois e manda para quem cantava essa com voce."
            ),
            "hashtags": ["#NostalgiaMusical", "#CarJams", "#Anos2010"],
        }
        assert approve(record)["status"] == "approved"
        assert record["approved_at"]

    def test_warnings_block_approval(self):
        record = {"tiktok_id": "111", "caption": "veio do tiktok", "hashtags": ["#a"]}
        with pytest.raises(CaptionError, match="impedem a aprovacao"):
            approve(record)
        assert record.get("status") != "approved"

    def test_force_overrides_the_gate_but_keeps_the_warnings(self):
        record = {"tiktok_id": "111", "caption": "veio do tiktok", "hashtags": ["#a"]}
        approve(record, force=True)
        assert record["status"] == "approved"
        assert record["warnings"], "os avisos precisam continuar visiveis"


class TestCache:
    def test_unchanged_metadata_does_not_call_the_model_again(self, tmp_path):
        client = FakeClient()
        path = tmp_path / "111.json"

        record, fresh = draft("111", METADATA, path, client=client)
        captions_mod.save(record, path)
        assert fresh and client.calls == 1

        _, fresh_again = draft("111", METADATA, path, client=client)
        assert not fresh_again
        assert client.calls == 1, "regerou uma legenda que nao mudou"

    def test_changed_metadata_regenerates(self, tmp_path):
        client = FakeClient()
        path = tmp_path / "111.json"
        record, _ = draft("111", METADATA, path, client=client)
        captions_mod.save(record, path)

        _, fresh = draft("111", {**METADATA, "title": "outro titulo"}, path, client=client)
        assert fresh and client.calls == 2

    def test_texto_editado_a_mao_sobrevive_a_metadados_novos(self, tmp_path):
        """A guarda que protege as 194 legendas cortadas para a v2.

        Subir a PROMPT_VERSION invalida a impressao digital de todos os
        registros de uma vez. Sem esta guarda, o proximo ``draft-captions``
        acharia todas desatualizadas e reescreveria o corte manual em silencio,
        que e a unica falha aqui que ninguem notaria antes de o post ir ao ar.
        """
        client = FakeClient()
        path = tmp_path / "111.json"
        record, _ = draft("111", METADATA, path, client=client)
        record["caption"] = "Trevo 🍀"
        record["edited_by_human"] = True
        captions_mod.save(record, path)

        mantido, fresh = draft("111", {**METADATA, "title": "outro"}, path, client=client)

        assert not fresh and client.calls == 1
        assert mantido["caption"] == "Trevo 🍀"

    def test_force_reescreve_ate_o_que_foi_editado_a_mao(self, tmp_path):
        """A guarda protege de acidente, nao de intencao declarada."""
        client = FakeClient()
        path = tmp_path / "111.json"
        record, _ = draft("111", METADATA, path, client=client)
        record["edited_by_human"] = True
        captions_mod.save(record, path)

        _, fresh = draft("111", METADATA, path, client=client, force=True)
        assert fresh and client.calls == 2

    def test_force_ignores_the_cache(self, tmp_path):
        client = FakeClient()
        path = tmp_path / "111.json"
        record, _ = draft("111", METADATA, path, client=client)
        captions_mod.save(record, path)

        draft("111", METADATA, path, client=client, force=True)
        assert client.calls == 2

    def test_fingerprint_ignores_key_order(self):
        assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})

    def test_regenerating_keeps_the_previous_text_in_history(self, tmp_path):
        path = tmp_path / "111.json"
        first = FakeClient()
        record, _ = draft("111", METADATA, path, client=first)
        captions_mod.save(record, path)

        second = FakeClient({"caption": "outra legenda", "hashtags": ["#a"], "alt_text": "x"})
        updated, _ = draft("111", METADATA, path, client=second, force=True)

        assert updated["history"], "a versao anterior foi perdida"
        assert updated["history"][-1]["caption"] == record["caption"]


class TestGeneration:
    def test_only_metadata_is_sent_never_the_video(self, tmp_path):
        client = FakeClient()
        draft("111", METADATA, tmp_path / "111.json", client=client)

        sent = client.last_kwargs["messages"][0]["content"]
        assert "Toda Toda" in sent
        assert ".mp4" not in sent

    def test_schema_is_enforced_by_the_api_not_by_parsing(self, tmp_path):
        client = FakeClient()
        draft("111", METADATA, tmp_path / "111.json", client=client)

        schema = client.last_kwargs["output_config"]["format"]
        assert schema["type"] == "json_schema"
        assert schema["schema"]["required"] == ["caption", "hashtags", "alt_text"]

    def test_a_refusal_surfaces_instead_of_writing_an_empty_caption(self, tmp_path):
        client = FakeClient(stop_reason="refusal")
        with pytest.raises(CaptionError, match="recusou"):
            draft("111", METADATA, tmp_path / "111.json", client=client)

    def test_new_records_start_as_drafts(self, tmp_path):
        record, _ = draft("111", METADATA, tmp_path / "111.json", client=FakeClient())
        assert record["status"] == "draft"
        assert record["approved_at"] is None


class TestFullCaption:
    def test_hashtags_are_appended_below_the_text(self):
        record = {"caption": "Texto da legenda", "hashtags": ["#um", "#dois"]}
        assert captions_mod.full_caption(record) == "Texto da legenda\n\n#um #dois"

    def test_no_hashtags_leaves_no_trailing_blank_lines(self):
        assert captions_mod.full_caption({"caption": "So o texto", "hashtags": []}) == "So o texto"
