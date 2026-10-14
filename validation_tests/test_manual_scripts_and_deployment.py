import ast,os,subprocess,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
def test_legacy_database_smoke_is_disposable(tmp_path):
 env=dict(os.environ,DB_URL='sqlite:///'+str(tmp_path/'legacy.db'))
 result=subprocess.run([sys.executable,str(ROOT/'test_db.py')],cwd=ROOT,env=env,capture_output=True,text=True,timeout=20)
 assert result.returncode==0
 import sqlite3
 con=sqlite3.connect(tmp_path/'legacy.db')
 assert con.execute("SELECT qty,avg_price FROM positions WHERE symbol='AAPL'").fetchone()==(10,190.25)
 con.close()
def test_ingest_compatibility_functions_exist():
 names={node.name for node in ast.parse((ROOT/'tickers/mass_ingest_bot.py').read_text()).body if isinstance(node,ast.FunctionDef)}
 assert {'fetch_recent_1m_bars','upsert_bars','ingest_once'}<=names
@pytest.mark.parametrize('name',['kill_switch.py','flatten_all.py','simulate_alerts.py','simulate_ticker_watch.py','run_sims_loop.py'])
def test_manual_cli_help(name):
 result=subprocess.run([sys.executable,str(ROOT/name),'--help'],cwd=ROOT,capture_output=True,text=True,timeout=15)
 assert result.returncode==0 and 'usage:' in result.stdout.lower()
@pytest.mark.parametrize('name',['start_bot.sh','infra/startup.sh'])
def test_shell_syntax(name):assert subprocess.run(['bash','-n',str(ROOT/name)],capture_output=True).returncode==0
@pytest.mark.parametrize('name',['scripts/kill_switch.py','scripts/flatten_all.py'])
def test_documented_entrypoints_exist(name):assert (ROOT/name).is_file()
def test_systemd_service_has_watchdog_and_preflight():
 content=(ROOT/'deploy/lavish-bot.service').read_text()
 for setting in ['Type=notify','WatchdogSec=90','ExecStartPre=','Restart=on-failure','User=lavish']:assert setting in content

def test_synthetic_lab_service_has_separate_state_and_no_broker_runner():
 content=(ROOT/'deploy/lavish-paper-lab.service').read_text()
 for setting in ['Type=notify','WatchdogSec=90','ExecStartPre=','Restart=on-failure','StateDirectory=lavish-paper-lab','UMask=0077']:
  assert setting in content
 assert 'run_ingest.py' not in content and '--check' in content

def test_ci_executes_full_validation_and_pins_actions():
 content=(ROOT/'.github/workflows/tests.yml').read_text()
 assert 'requirements-validation.txt' in content and 'pytest -q --junitxml=' in content
 assert 'pytest tests/' not in content and 'bandit -r lavish_core ml -ll' in content
 assert 'persist-credentials: false' in content and 'contents: read' in content
 import re
 actions=re.findall(r'uses: ([^\s]+)',content)
 assert actions and all(re.search(r'@[a-f0-9]{40}$',action) for action in actions)

def test_workflow_inputs_enter_shell_through_environment():
 content=(ROOT/'.github/workflows/simulate-ticker-watch.yml').read_text()
 run=content.split('      - name: Run simulation')[1].split('      - name: Write job summary')[0]
 assert '--days "$SIM_DAYS"' in run and '--seed "$SIM_SEED"' in run
 assert '${{ github.event.inputs.' not in run.split('        run:')[1]
