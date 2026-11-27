"""Keep assistant-authored templates independent of the chosen TTS voice.

This guards owned source literals, never a rendered sentence. User titles,
entity names and external provider speech must retain their original wording.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "custom_components/polish_context_voice"
SELF_GENDER = re.compile(
    r"\b(?:by|mog|chcia|doda|odwoła|znalaz|rozpozna|zmieni|zapisa|przełoży|"
    r"wykona|uruchomi|włączy|wyłączy|sprawdzi|odczyta|ustawi|zatrzyma|potwierdzi|"
    r"anulowa|zakończy|przerwa|zrobi)(?:łem|łam)\b|"
    r"\bchciał(?:bym|abym)\b|\bjestem\s+gotow[ya]\b",
    re.IGNORECASE,
)


def test_owned_runtime_templates_do_not_assign_gender_to_the_assistant():
    findings = []
    for source in sorted(ROOT.rglob("*.py")):
        module = ast.parse(source.read_text())
        for node in ast.walk(module):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if SELF_GENDER.search(node.value):
                    findings.append(f"{source.relative_to(ROOT)}:{node.lineno}: {node.value}")
    assert not findings, "Use neutral owned speech templates:\n" + "\n".join(findings)
