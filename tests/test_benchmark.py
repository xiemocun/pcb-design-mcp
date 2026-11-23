"""No positive score from missing evidence, changed tasks, or cherry-picked pairs."""
import tempfile
from pathlib import Path
import unittest
from pcb_mcp_client.benchmark import Task,grade,freeze,verify_freeze,paired_comparison


class Benchmark(unittest.TestCase):
    def task(self):
        return Task(id='example',category='analog',split='development',origin='developer_authored',
                    source='Synthetic fixture for grader integrity only.',requirement_sha256='a'*64,
                    contract_sha256='b'*64,requirement_ids=['gain'])

    def test_missing_reports_cannot_pass(self):
        with tempfile.TemporaryDirectory() as d:self.assertFalse(grade(self.task(),Path(d))['passed'])

    def test_run_evidence_cannot_escape_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'RUN_OUTSIDE'):
                grade(self.task(),Path(d),run_relative='../other/run')

    def test_unknown_functional_coverage_cannot_be_a_task(self):
        raw=self.task().model_dump();raw['requirement_ids']=[]
        with self.assertRaises(ValueError):Task.model_validate(raw)

    def test_added_or_modified_implementation_invalidates_freeze(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'src').mkdir();(root/'src/a.py').write_text('baseline')
            frozen=freeze(root);self.assertTrue(verify_freeze(root,frozen)['passed'])
            (root/'src/new.py').write_text('adaptation to seen task')
            self.assertFalse(verify_freeze(root,frozen)['passed'])

    def test_comparison_cannot_drop_a_failed_case(self):
        ours=[dict(task_id='one',task_sha256='a',passed=True),dict(task_id='two',task_sha256='b',passed=False)]
        with self.assertRaisesRegex(ValueError,'TASK_SETS_DIFFER'):paired_comparison(ours,ours[:1])
        with self.assertRaisesRegex(ValueError,'DUPLICATE'):paired_comparison(ours,ours+ours[:1])

    def test_equal_results_do_not_claim_superiority(self):
        rows=[dict(task_id=str(i),task_sha256=str(i),passed=i%2==0) for i in range(40)]
        result=paired_comparison(rows,rows)
        self.assertFalse(result['positive_success_rate_evidence'])
        self.assertEqual(result['paired_bootstrap_95_interval'],[0.,0.])

    def test_unequal_requirement_identity_cannot_be_compared(self):
        with self.assertRaisesRegex(ValueError,'REQUIREMENTS_DIFFER'):
            paired_comparison([dict(task_id='a',task_sha256='a',passed=True)],
                              [dict(task_id='a',task_sha256='b',passed=False)])

    def pairs(self,wins=40,clustered=False):
        ours=[dict(task_id=str(i),task_sha256=str(i),cluster_id='family' if clustered else 'family_'+str(i),passed=i<wins) for i in range(40)]
        theirs=[dict(r,passed=False) for r in ours]
        return ours,theirs

    def test_four_discordant_wins_do_not_establish_advantage(self):
        result=paired_comparison(*self.pairs(wins=4))
        self.assertAlmostEqual(result['exact_cluster_label_swap_two_sided_p'],.125)
        self.assertFalse(result['positive_success_rate_evidence'])

    def test_repeated_one_family_is_not_forty_independent_wins(self):
        result=paired_comparison(*self.pairs(clustered=True))
        self.assertEqual(result['clusters'],1);self.assertEqual(result['exact_cluster_label_swap_two_sided_p'],1.)
        self.assertFalse(result['positive_success_rate_evidence'])

    def test_broad_paired_wins_with_declared_families_can_establish_advantage(self):
        result=paired_comparison(*self.pairs())
        self.assertTrue(result['positive_success_rate_evidence']);self.assertEqual(result['paired_bootstrap_95_interval'],[1.,1.])
        self.assertAlmostEqual(result['exact_cluster_label_swap_two_sided_p'],2/2**40)

    def test_changed_clusters_and_nonboolean_outcomes_are_refused(self):
        ours,theirs=self.pairs();theirs[0]['cluster_id']='other'
        with self.assertRaisesRegex(ValueError,'CLUSTERS_DIFFER'):paired_comparison(ours,theirs)
        theirs[0]['cluster_id']=ours[0]['cluster_id'];theirs[0]['passed']='false'
        with self.assertRaisesRegex(ValueError,'NOT_BOOLEAN'):paired_comparison(ours,theirs)

    def test_repeated_formal_looks_spend_prespecified_alpha(self):
        ours,theirs=self.pairs(wins=8)
        first=paired_comparison(ours,theirs,1);third=paired_comparison(ours,theirs,3)
        self.assertAlmostEqual(first['alpha_for_this_look'],.025)
        self.assertAlmostEqual(third['alpha_for_this_look'],.05/12)
        self.assertTrue(first['positive_success_rate_evidence']);self.assertFalse(third['positive_success_rate_evidence'])

    def test_mandatory_rejections_cannot_inflate_complete_task_success(self):
        ours,theirs=self.pairs();ours[0]['expected']='reject'
        with self.assertRaisesRegex(ValueError,'REJECTION_CASES_NOT_SUCCESS_RATE_TASKS'):paired_comparison(ours,theirs)


if __name__=='__main__':unittest.main()
