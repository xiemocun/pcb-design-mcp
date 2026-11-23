from pathlib import Path
import os
import signal
import tempfile
import unittest
from unittest.mock import patch, Mock
from pcb_mcp_client.process_job import Process, belongs, stop_marked, run_job

class ProcessJobTests(unittest.TestCase):
    marker='/tmp/pcb-job-unique/chain.cir'
    def proc(self, pid=900001, uid=None, command=None, started='Mon Nov 2 12:00:00 2026'):
        return Process(pid,os.getuid() if uid is None else uid,started,
                       command or f'LTspice.exe -b -run Z:{self.marker}')

    def test_native_windows_and_quoted_paths(self):
        for arg in (self.marker, 'Z:'+self.marker, 'Z:'+self.marker.replace('/','\\'), '"Z:'+self.marker+'"'):
            self.assertTrue(belongs(self.proc(command='LTspice.exe -b '+arg),self.marker))

    def test_other_jobs_users_and_prefix_collision_untouched(self):
        for p in (self.proc(uid=os.getuid()+1),self.proc(command='LTspice.exe -b '+self.marker+'.other'),
                  self.proc(command='LTspice.exe -b /tmp/another.cir'),self.proc(pid=os.getpid())):
            self.assertFalse(belongs(p,self.marker))

    def test_detached_child_is_stopped_without_shared_wineserver(self):
        child=self.proc();other=self.proc(pid=900002,command='wineserver -p')
        rows=[child,other];sent=[]
        def send(pid,sig):sent.append((pid,sig));rows[:]=[p for p in rows if p.pid!=pid]
        r=stop_marked(self.marker,grace_s=0,list_processes=lambda:list(rows),send_signal=send)
        self.assertEqual(sent,[(child.pid,signal.SIGTERM)])
        self.assertEqual(rows,[other]);self.assertTrue(r['verified_stopped'])

    def test_stubborn_process_escalates_and_reports_survivor(self):
        p=self.proc();sent=[]
        r=stop_marked(self.marker,grace_s=0,list_processes=lambda:[p],send_signal=lambda pid,s:sent.append(s))
        self.assertEqual(sent,[signal.SIGTERM,signal.SIGKILL]);self.assertFalse(r['verified_stopped'])

    def test_pid_reuse_is_not_signalled(self):
        old=self.proc();new=self.proc(command='unrelated',started='Mon Nov 2 13:00:00 2026')
        calls=[0];sent=[]
        def rows():calls[0]+=1;return [old] if calls[0]==1 else [new]
        r=stop_marked(self.marker,grace_s=0,list_processes=rows,send_signal=lambda *x:sent.append(x))
        self.assertEqual(sent,[]);self.assertTrue(r['verified_stopped'])

    def test_no_process_inspection_means_no_launch(self):
        with patch('pcb_mcp_client.process_job.snapshot',side_effect=PermissionError), patch('pcb_mcp_client.process_job.subprocess.Popen') as launch:
            with self.assertRaises(PermissionError):
                run_job(['not-launched'],self.marker,'unused.log',timeout_s=1)
            launch.assert_not_called()

    def test_timeout_with_unverified_cleanup_cannot_report_success(self):
        child=self.proc();p=Mock(pid=900003,returncode=-9);p.poll.return_value=None
        cleanup={'verified_stopped':False,'survivors':[child.pid],'signals':[]}
        with tempfile.TemporaryDirectory() as directory, \
             patch('pcb_mcp_client.process_job.snapshot',side_effect=[[],[child]]), \
             patch('pcb_mcp_client.process_job.subprocess.Popen',return_value=p) as launch, \
             patch('pcb_mcp_client.process_job.time.monotonic',side_effect=[0,2,3]), \
             patch('pcb_mcp_client.process_job.stop_marked',return_value=cleanup), \
             patch('pcb_mcp_client.process_job.os.killpg') as killgroup:
            result=run_job(['fixture'],self.marker,Path(directory)/'run.log',timeout_s=1)
        self.assertEqual(result['status'],'cleanup_unverified')
        self.assertFalse(result['cleanup']['verified_stopped'])
        self.assertEqual(result['observed_processes'][0]['pid'],child.pid)
        self.assertTrue(launch.call_args.kwargs['start_new_session'])
        killgroup.assert_called_once_with(p.pid,signal.SIGKILL)

    def test_exited_wrapper_is_reaped_without_redundant_group_signal(self):
        child=self.proc();p=Mock(pid=900003,returncode=-15);p.poll.side_effect=[None,-15]
        cleanup={'verified_stopped':True,'survivors':[],'signals':[]}
        with tempfile.TemporaryDirectory() as directory, \
             patch('pcb_mcp_client.process_job.snapshot',side_effect=[[],[child]]), \
             patch('pcb_mcp_client.process_job.subprocess.Popen',return_value=p), \
             patch('pcb_mcp_client.process_job.time.monotonic',side_effect=[0,2,3]), \
             patch('pcb_mcp_client.process_job.stop_marked',return_value=cleanup), \
             patch('pcb_mcp_client.process_job.os.killpg',side_effect=PermissionError) as killgroup:
            result=run_job(['fixture'],self.marker,Path(directory)/'run.log',timeout_s=1)
        self.assertEqual(result['status'],'timeout')
        self.assertTrue(result['cleanup']['verified_stopped'])
        killgroup.assert_not_called();p.wait.assert_called_once_with(timeout=5)

if __name__=='__main__':unittest.main()
