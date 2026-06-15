"""Regression tests for the Image Builder CloudFormation template."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AMI_TEMPLATE = REPO_ROOT / "ami" / "imagebuilder-template.yaml"
SUB_TOKEN_RE = re.compile(r"\$\{([^}]+)\}")
ALLOWED_SUB_TOKENS = {
    "Environment",
    "LlamaCppDockerImage",
}


def test_imagebuilder_template_sub_tokens_are_cloudformation_safe():
    """Embedded Bash in !Sub must not contain unescaped shell ${...} forms."""
    text = AMI_TEMPLATE.read_text()
    invalid: list[str] = []

    for match in SUB_TOKEN_RE.finditer(text):
        token = match.group(1)
        if token.startswith("!"):
            token = token[1:]
        if token == "imagebuilder:buildDate":
            continue
        if token not in ALLOWED_SUB_TOKENS:
            invalid.append(match.group(0))

    assert invalid == []
