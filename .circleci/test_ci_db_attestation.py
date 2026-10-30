"""Synthetic-only read-only MariaDB attestation gates (no actual SQL connection)."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import ci_db_attestation as db
import restore_guard as guard


def approved():
    return {
        'CIRCLECI':'true','CIRCLE_PROJECT_USERNAME':'CCPCerp_2026',
        'CIRCLE_PROJECT_REPONAME':'ccpcerp','CIRCLE_BRANCH':guard.BRANCH,
        'CIRCLE_JOB':guard.JOB,'TEST_SITE':guard.SITE,
        'CCPC_ALLOW_INTEGRATION2_RESTORE':'approved-only',
        'CIRCLE_PIPELINE_ID':'pipe','CIRCLE_WORKFLOW_ID':'job-alpha',
        'CIRCLE_BUILD_NUM':'9','CIRCLE_SHA1':'f'*40,
        'CCPC_RESTORE_APPROVED_SHA':'f'*40,
        'CCPC_RESTORE_OWNER_DATA_EGRESS_APPROVED':'yes',
        'CCPC_RESTORE_SSH_DEBUGGING_DISABLED':'yes',
    }


class DBAttestationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.sec=self.root/'private';self.sec.mkdir(mode=0o700)
        self.opt=self.sec/'db-client.cnf';self.opt.write_text('[client]\nuser=synthetic\n');self.opt.chmod(0o600)
        self.client=Path('/usr/bin/mariadb')
        self.bench=self.root/'bench'
        self.site=self.bench/'sites'/guard.SITE
        self.site.mkdir(parents=True)
        conf=self.site/'site_config.json'
        conf.write_text(json.dumps({
            'db_name':db.DB_NAME,'db_user':db.DB_USER,'db_host':db.DB_HOST,
            'db_port':db.DB_PORT,'db_password':'synthetic-only',
        }))
        conf.chmod(0o600)

    def probe(self, count=0, server='ephemeral-mariadb-123',version='11.8.5-MariaDB'):
        return Mock(stdout=f'{server}\t{version}\t3306\t1\t0\troot@%\t{count}\n')

    def test_attests_absence_before_creation(self):
        with patch.object(db, '_local_mariadb_probe', return_value=(('ephemeral','11.8.5-MariaDB','1'),0)):
            p=db.attest_before_new_site(approved(),'f'*40,option_file=self.opt)
            self.assertEqual(p.circle_workflow_id,'job-alpha')
            self.assertEqual(p.approved_checkout_sha,'f'*40)

    def test_refuses_database_already_present(self):
        with patch.object(db, '_local_mariadb_probe', return_value=(('ephemeral','11.8.5-MariaDB','1'),1)):
            with self.assertRaises(RuntimeError):db.attest_before_new_site(approved(),'f'*40,option_file=self.opt)

    def test_checks_identity_before_database_probe(self):
        with patch.object(db, '_local_mariadb_probe') as mock:
            env=approved();env['CCPC_RESTORE_APPROVED_SHA']='e'*40
            with self.assertRaises(RuntimeError):db.attest_before_new_site(env,'f'*40,option_file=self.opt)
            mock.assert_not_called()

    def test_matches_same_db_instance_and_site_after_creation(self):
        proof=db.AbsentDatabaseProof(('ephemeral','11.8.5-MariaDB','1'),'job-alpha','f'*40)
        with patch.object(db, '_local_mariadb_probe', return_value=(('ephemeral','11.8.5-MariaDB','1'),1)) as mock:
            db.attest_before_restore(approved(),'f'*40,proof,option_file=self.opt,bench=self.bench)
            self.assertEqual(mock.call_count,1)

    def test_fails_if_second_server_mismatch_or_db_absent(self):
        proof=db.AbsentDatabaseProof(('ephemeral','11.8.5-MariaDB','1'),'job-alpha','f'*40)
        for fingerprint,count in [(('different','11.8.5-MariaDB','1'),1),(('ephemeral','11.8.5-MariaDB','1'),0)]:
            with self.subTest(fingerprint=fingerprint,count=count),patch.object(db,'_local_mariadb_probe',return_value=(fingerprint,count)):
                with self.assertRaises(RuntimeError):db.attest_before_restore(approved(),'f'*40,proof,option_file=self.opt,bench=self.bench)

    def test_rejects_site_config_wrong_host_name_user_or_port(self):
        proof=db.AbsentDatabaseProof(('ephemeral','11.8.5-MariaDB','1'),'job-alpha','f'*40)
        config=self.site/'site_config.json'
        original=json.loads(config.read_text())
        for k,v in [('db_host','192.168.50.166'),('db_name','development'),('db_user','root'),('db_port',3307),('redis_queue','redis://prod'),
                    ('db_socket','/var/run/mysqld/mysqld.sock'),('mariadb_socket','/tmp/socket'),
                    ('db_type','postgres'),('db_driver','sqlite')]:
            with self.subTest(k=k):
                conf=dict(original);conf[k]=v;config.write_text(json.dumps(conf))
                with patch.object(db,'_local_mariadb_probe') as mock:
                    with self.assertRaises(RuntimeError):db.attest_before_restore(approved(),'f'*40,proof,option_file=self.opt,bench=self.bench)
                    mock.assert_not_called()
        config.write_text(json.dumps(original))

    def test_rejects_site_symlink_or_option_file_bad_mode(self):
        proof=db.AbsentDatabaseProof(('ephemeral','11.8.5-MariaDB','1'),'job-alpha','f'*40)
        self.opt.chmod(0o644)
        with self.assertRaises(RuntimeError):db._owner_file(self.opt)
        self.opt.chmod(0o600)
        self.site.rename(self.bench/'sites'/'real')
        self.site.symlink_to(self.bench/'sites'/'real',target_is_directory=True)
        with self.assertRaises(RuntimeError):db.attest_before_restore(approved(),'f'*40,proof,option_file=self.opt,bench=self.bench)

    def test_probe_query_has_fixed_schema_and_no_user_interpolation(self):
        self.assertIn('information_schema.SCHEMATA',db.PROBE_SQL)
        self.assertNotIn('{',db.PROBE_SQL)
        self.assertIn(db.DB_NAME,db.PROBE_SQL)
        self.assertNotIn('DROP ',db.PROBE_SQL.upper())
        self.assertNotIn('CREATE ',db.PROBE_SQL.upper())

    def test_cli_output_parsing_and_no_leaked_errors(self):
        with patch.object(db.Path,'is_file',return_value=True), patch.object(db.Path,'is_symlink',return_value=False),patch('subprocess.run',return_value=self.probe(count=0)) as run:
            val,count=db._local_mariadb_probe(self.opt,self.client)
            self.assertEqual(count,0);self.assertEqual(val[0],'ephemeral-mariadb-123')
            argv=run.call_args.args[0]
            self.assertEqual(argv[0],'/usr/bin/mariadb')
            self.assertTrue(argv[1].startswith('--defaults-file='))
            self.assertIn('--host=127.0.0.1',argv)
            self.assertNotIn('password=synthetic',str(argv))
            self.assertNotIn('MYSQL_PWD',run.call_args.kwargs['env'])
            # A bad output row cannot be passed as proof.
        with patch('subprocess.run',return_value=self.probe(server='ccpcerpserver')):
            with self.assertRaises(RuntimeError):db._local_mariadb_probe(self.opt,self.client)

    def test_no_destructive_runner_in_attestation_module(self):
        src=Path(db.__file__).read_text()
        for t in ('bench restore','subprocess.Popen(', 'execute_bench_restore(', 'CREATE DATABASE', 'DROP DATABASE', 'db_probe=lambda'):
            self.assertNotIn(t,src)

if __name__=='__main__': unittest.main()
