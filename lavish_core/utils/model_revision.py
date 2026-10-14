"""Optional downloads require an operator-selected immutable model revision."""
import os,re

def required_revision(variable):
    revision=os.getenv(variable,'')
    if not re.fullmatch(r'[a-fA-F0-9]{40}',revision):raise ValueError(f'{variable} must name a full model commit SHA')
    return revision
