import os, stat
from datetime import datetime,timezone,timedelta
from unittest.mock import Mock
import pytest, requests
from lavish_core.patreon import patreon_refresh as pr
from lavish_core.patreon import patreon_trigger as pt
from lavish_core.utils import alerts
@pytest.fixture
def oauth_fixture(tmp_path,monkeypatch):
 monkeypatch.chdir(tmp_path);monkeypatch.setenv('LAVISH_ENV_FILE',str(tmp_path/'.env'))
 for k,v in [('PATREON_CLIENT_ID','dummy'),('PATREON_CLIENT_SECRET','dummy'),('PATREON_REFRESH_TOKEN','oldrefresh'),('PATREON_ACCESS_TOKEN','oldaccess')]:monkeypatch.setenv(k,v)
 return tmp_path
@pytest.mark.parametrize('status',[200,400,401,403,429,500,502,503])
def test_oauth_status(oauth_fixture,monkeypatch,status):
 monkeypatch.setattr(pr.requests,'post',Mock(return_value=Mock(status_code=status,json=lambda:{'access_token':'newaccess','refresh_token':'newrefresh'})))
 assert pr.refresh_patreon_token()==('newaccess' if status==200 else None)
 assert os.environ['PATREON_ACCESS_TOKEN']==('newaccess' if status==200 else 'oldaccess')
 if status==200:assert (oauth_fixture/'.env').exists()
@pytest.mark.parametrize('body',[{}, {'error':'invalid_grant'},{'error':'rate_limited'},{'access_token':'newaccess'},{'refresh_token':'newrefresh'},[],None,'bad-json-shape',1])
def test_oauth_body(oauth_fixture,monkeypatch,body):
 monkeypatch.setattr(pr.requests,'post',Mock(return_value=Mock(status_code=200,json=lambda:body)))
 assert pr.refresh_patreon_token() is None
 assert os.environ['PATREON_ACCESS_TOKEN']=='oldaccess'
@pytest.mark.parametrize('exc',[requests.Timeout(),requests.ConnectionError()])
def test_oauth_transport(oauth_fixture,monkeypatch,exc):
 monkeypatch.setattr(pr.requests,'post',Mock(side_effect=exc));assert pr.refresh_patreon_token() is None
 assert os.environ['PATREON_ACCESS_TOKEN']=='oldaccess'
def test_oauth_permissions(oauth_fixture):
 pr._update_env('PATREON_ACCESS_TOKEN','dummy');assert stat.S_IMODE((oauth_fixture/'.env').stat().st_mode)&0o077==0
def test_oauth_no_token_logging(oauth_fixture,monkeypatch):
 marker='SYNTHETIC_TOKEN_LOG_SENTINEL_NOT_REAL';warning=Mock();monkeypatch.setattr(pr.log,'warning',warning)
 monkeypatch.setattr(pr.requests,'post',Mock(return_value=Mock(status_code=200,json=lambda:{'access_token':marker})))
 assert pr.refresh_patreon_token() is None;assert marker not in str(warning.call_args_list)
def test_oauth_durable_before_environment(oauth_fixture,monkeypatch):
 monkeypatch.setattr(pr.requests,'post',Mock(return_value=Mock(status_code=200,json=lambda:{'access_token':'newaccess','refresh_token':'newrefresh'})))
 monkeypatch.setattr(pr,'_update_env',Mock(side_effect=PermissionError()))
 assert pr.refresh_patreon_token() is None;assert os.environ['PATREON_ACCESS_TOKEN']=='oldaccess'
@pytest.mark.parametrize('age',[0,30,899,901,3600,-60])
def test_post_age(age):
 stamp=(datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat()
 assert pt._post_age_seconds({'attributes':{'created_at':stamp}})==pytest.approx(age,abs=1)
@pytest.mark.parametrize('url',['http://127.0.0.1/a.png','http://169.254.169.254/a.png','http://10.0.0.1/a.png','http://localhost/a.png'])
def test_private_image_urls(tmp_path,monkeypatch,url):
 get=Mock();monkeypatch.setattr(pt.requests,'get',get)
 monkeypatch.setattr(pt,'VISION_RAW',tmp_path)
 pt._download_images_from_post({'attributes':{'content':f'<img src="{url}">'}})
 get.assert_not_called()
@pytest.mark.parametrize('status',[200,204,400,401,403,429,500,503])
def test_webhook_response(monkeypatch,status):
 monkeypatch.setattr(alerts,'WEBHOOK','https://example.invalid/webhook');post=Mock(return_value=Mock(status_code=status,text='synthetic'));monkeypatch.setattr(alerts.requests,'post',post)
 alerts.post_discord('synthetic');post.assert_called_once()
def test_webhook_timeout(monkeypatch):
 monkeypatch.setattr(alerts,'WEBHOOK','https://example.invalid/webhook');monkeypatch.setattr(alerts.requests,'post',Mock(side_effect=requests.Timeout()));alerts.post_discord('synthetic')
def test_missing_webhook(monkeypatch):
 monkeypatch.setattr(alerts,'WEBHOOK','');post=Mock();monkeypatch.setattr(alerts.requests,'post',post);alerts.post_discord('synthetic');post.assert_not_called()
