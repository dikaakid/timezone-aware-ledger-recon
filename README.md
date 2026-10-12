cd -LiteralPath "C:\Users\User\Downloads\Non Job\SCRIPT\BRIVA\timezone-aware-ledger-recon"

# Cek metadata
Select-String -Path .\pyproject.toml -Pattern '^(name|version|description)'
# Expected:
# name = "timezone-aware-ledger-recon"
# version = "0.1.0"
# description = "Reconcile bank, ledger, and verification sources that disagree on timezone."

# Cek __init__.py
Select-String -Path .\ledger_recon\__init__.py -Pattern '__version__'
# Expected: __version__ = "0.1.0"

# Cek CHANGELOG (baris atas)
Get-Content .\CHANGELOG.md -TotalCount 8

# Test — harus tetap lulus 41
python -m pytest -q