"""Fresh non-ASCII/spaced portable launch and safe one-click handover checks."""
from pathlib import Path
import contextlib
import hashlib
import http.client
import importlib.util
import io
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
PACKAGE=Path(__file__).resolve().parents[1]

class HandoverTests(unittest.TestCase):
    def test_one_click_launcher_exists_and_open_is_explicit(self):
        self.assertTrue((PACKAGE/'Start Dashboard.cmd').is_file(),'D07 needs easy launch entrypoint')
        help_result=subprocess.run([sys.executable,str(PACKAGE/'tools/run_dashboard.py'),'--help'],capture_output=True,text=True,timeout=5)
        self.assertIn('--open',help_result.stdout)

    def test_browser_open_uses_only_loopback_url_and_cleanup(self):
        spec=importlib.util.spec_from_file_location('dashboard_launch_check',PACKAGE/'tools/run_dashboard.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='dashboard-state-') as state_dir:
            with patch.dict(os.environ, {'TWIN_STATE_DB_PATH': str(Path(state_dir)/'twin.sqlite3')}):
                with patch.object(sys,'argv',['run_dashboard.py','--port','0','--open']),patch.object(sys,'stdin',io.StringIO('test-handover-credential!\nquit\n')),contextlib.redirect_stdout(io.StringIO()) as output,patch('webbrowser.open',return_value=True) as opened:
                    self.assertEqual(module.main(),0)
        self.assertEqual(opened.call_count,1)
        self.assertRegex(opened.call_args.args[0],r'^http://127\.0\.0\.1:[0-9]+$')
        self.assertNotIn('test-handover-credential!',output.getvalue())
        self.assertIn('Owner stopped',output.getvalue())

    def test_offline_fresh_spaced_non_ascii_folder(self):
        temp_root=PACKAGE.parent/'tmp/dashboard_remaining';temp_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='Portable city – تجربة ',dir=temp_root) as directory:
            target=Path(directory).resolve();self.assertTrue(target.is_relative_to(temp_root.resolve()))
            app=target/'person_a';shutil.copytree(PACKAGE/'src',app/'src',ignore=shutil.ignore_patterns('__pycache__'))
            (app/'tools').mkdir();shutil.copy2(PACKAGE/'tools/run_dashboard.py',app/'tools/run_dashboard.py')
            proc=subprocess.Popen([sys.executable,'-X','utf8',str(app/'tools/run_dashboard.py'),'--port','0'],cwd=target,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
            lines=[];ready=queue.Queue()
            def collect():
                for line in proc.stdout:lines.append(line);ready.put(line)
            reader=threading.Thread(target=collect,daemon=True);reader.start()
            port=None
            try:
                proc.stdin.write('portable-local-test-credential!\n');proc.stdin.flush()
                line=ready.get(timeout=8);self.assertIn('http://127.0.0.1:',line);port=int(line.rsplit(':',1)[1])
                def request(method,path,body=None,cookie=None):
                    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
                    headers={'Origin':f'http://127.0.0.1:{port}'}
                    if body is not None:body=json.dumps(body);headers['Content-Type']='application/json'
                    if cookie:headers['Cookie']=cookie
                    try:
                        conn.request(method,path,body,headers);response=conn.getresponse();return response.status,dict(response.getheaders()),response.read()
                    finally:conn.close()
                for path in ('/','/styles.css','/app.js','/api.js','/store.js','/map.js','/panels.js','/controls.js','/history.js','/sensors.js','/timeline.js','/map-layout.json','/city-map.svg'):
                    status,headers,body=request('GET',path);self.assertEqual(status,200,path);self.assertEqual(headers['Cache-Control'],'no-store');self.assertTrue(body)
                status,headers,_=request('POST','/api/session',{'secret':'portable-local-test-credential!'});self.assertEqual(status,200)
                cookie=headers['Set-Cookie'].split(';')[0]
                status,_,raw=request('GET','/api/operator/frame',cookie=cookie);self.assertEqual(status,200);f=json.loads(raw)['data']
                status,_,raw=request('POST','/api/operator/command',{'command_id':'portable-pause','run_id':f['run_id'],'expected_revision':None,'kind':'pause','args':{}},cookie);self.assertEqual(status,200)
                self.assertEqual(json.loads(raw)['data']['state']['state'],'PAUSED')
            finally:
                proc.stdin.write('quit\n');proc.stdin.flush();proc.stdin.close()
                try:proc.wait(timeout=8)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3);raise
                reader.join(timeout=3);stderr=proc.stderr.read();proc.stdout.close();proc.stderr.close()
            self.assertEqual(proc.returncode,0,stderr);self.assertEqual(stderr,'')
            self.assertIn('Owner stopped.',''.join(lines));self.assertNotIn('portable-local-test-credential!',''.join(lines))
            with socket.socket() as conn:self.assertNotEqual(conn.connect_ex(('127.0.0.1',port)),0)
