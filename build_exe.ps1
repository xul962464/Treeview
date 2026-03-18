param(
  [string]$Name = "PhyloTreeViewer"
)

python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt

pyinstaller -F -w -n $Name app/main.py

