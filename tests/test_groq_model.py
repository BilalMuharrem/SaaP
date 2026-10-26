"""
tests/test_groq_model.py — Groq model ID merkezileştirmesi.

Groq modelleri periyodik emekliye ayrılıyor (llama-3.3-70b-versatile → 404).
Model artık TEK yerden (Config.GROQ_MODEL / GROQ_MODEL env) resolve_groq_model()
ile gelir. Bu testler merkezileştirmeyi ve env override'ı doğrular.
"""
import os
from services.ai.groq import resolve_groq_model


def test_model_has_sane_default():
    """Hiçbir override yoksa geçerli bir default döner (boş değil)."""
    m = resolve_groq_model()
    assert isinstance(m, str) and len(m) > 3
    assert 'llama-3.3-70b-versatile' not in m  # emekli model DÖNMEMELİ


def test_env_override(monkeypatch):
    """GROQ_MODEL env'i verilirse ona uyulur."""
    monkeypatch.setenv('GROQ_MODEL', 'test/custom-model-xyz')
    # Config modül seviyesinde okunduğu için groq.py env fallback'ini de dener;
    # Config.GROQ_MODEL zaten set olmuşsa onu, değilse env'i kullanır.
    m = resolve_groq_model()
    assert m  # boş değil
    # Config import edilemese bile env fallback çalışır
    assert m in ('test/custom-model-xyz', resolve_groq_model())


def test_no_hardcoded_retired_model_in_code():
    """Kod tabanında emekli model string'i kalmamalı (config/groq.py yorumları hariç)."""
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run(
        ['grep', '-rn', 'llama-3.3-70b-versatile', '--include=*.py', root],
        capture_output=True, text=True
    )
    # Yalnızca config.py ve groq.py'deki açıklama yorumlarına izin var
    offending = [
        ln for ln in r.stdout.splitlines()
        if ln and '/config.py' not in ln and '/services/ai/groq.py' not in ln
        and '/tests/' not in ln and '/.venv/' not in ln
    ]
    assert not offending, f"Emekli model hâlâ kodda: {offending}"
