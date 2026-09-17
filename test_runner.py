import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import runner

TASKS = '## T-001\n목표: 테스트 항목\n검증: 단위 테스트\n상태: pending\n'


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.work = self.repo / 'work'
        (self.work / 'runs').mkdir(parents=True)
        self.runtime = self.repo / 'runtime'
        self.runtime.mkdir()
        self.state_path = self.runtime / 'state.json'
        self.args = argparse.Namespace(issue='123', resume=False, feedback='', max_calls=5,
                                       max_task_attempts=3, direction='down', design_kind='claude',
                                       implement_kind='codex', timeout=30, design_turns=0,
                                       stop_after='review', iterations=1)
        self.calls = []
        self.pane = None
        self.panes = set()
        self.status = 'idle'
        self.fail_prompt = False
        self.continue_review = False
        self.skip_review_record = False
        self.phases = []

    def state(self):
        return json.loads(self.state_path.read_text())

    def loop(self):
        runner.run(self.args, self.repo, self.work, self.runtime)

    def fake_herdr(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ('pane', 'split'):
            self.pane = f'w1:p{len(self.calls)}'
            self.panes.add(self.pane)
            return {'pane': {'pane_id': self.pane}}
        if args[:2] == ('pane', 'list'):
            return {'panes': [{'pane_id': p} for p in self.panes]}
        if args[:2] == ('agent', 'list'):
            return {'agents': [{'pane_id': p, 'agent_status': self.status} for p in self.panes]}
        if args[:2] == ('pane', 'close'):
            self.panes.discard(args[2])
        if args[:2] == ('agent', 'prompt'):
            if self.fail_prompt:
                raise RuntimeError('timeout')
            state = self.state()
            self.phases.append(state['phase'])
            (self.work / 'design.md').write_text('설계 근거')
            (self.work / 'tasks.md').write_text(TASKS)
            action = {'design': 'implement', 'implement': 'review', 'review': 'complete'}[state['phase']]
            if state['phase'] == 'review' and self.continue_review:
                action = 'implement'
            if state['phase'] == 'implement':
                (self.work / 'progress.md').write_text(
                    f'## {state["task"]}\n### 구현 결과\nrun_id: {state["run_id"]}\n검증 통과\n')
            if state['phase'] == 'review' and not self.skip_review_record:
                # An interrupted implementation may have left no document at all.
                written = self.work / 'progress.md'
                progress = written.read_text() if written.exists() else f'## {state["task"]}\n'
                progress += f'### 설계 검토\nrun_id: {state["review_target"]}\n판정: 통과\n'
                if action == 'complete':
                    progress += '## 최종 검토\n수용 조건 근거 확인\n'
                (self.work / 'progress.md').write_text(progress)
            result = {k: state[k] for k in ('run_id', 'phase', 'task')}
            result.update(action=action, next_task='T-001' if action == 'implement' else None,
                          summary='checked')
            runner.save(self.work / 'runs' / (state['run_id'] + '.json'), result)
        return {}

    def test_persistent_designer_and_fresh_implementer(self):
        with patch.object(runner, 'herdr', self.fake_herdr):
            self.loop()
        starts = [c for c in self.calls if c[:2] == ('agent', 'start')]
        self.assertEqual(len(starts), 2)
        self.assertEqual(len({c[2] for c in starts}), 2)
        self.assertEqual(len([c for c in self.calls if c[:2] == ('pane', 'close')]), 1)
        prompts = [c[2] for c in self.calls if c[:2] == ('agent', 'prompt')]
        self.assertEqual(prompts[0], prompts[2])
        self.assertEqual(self.state()['status'], 'complete')

    def test_runner_state_lives_outside_the_worktree(self):
        with patch.object(runner, 'herdr', self.fake_herdr):
            self.loop()
        self.assertTrue(self.state_path.is_file())
        self.assertFalse((self.work / 'runner-state.json').exists())
        self.assertEqual(self.state()['work'], str(self.work))

    def test_timeout_preserves_pane_and_requires_recovery(self):
        self.fail_prompt = True
        with patch.object(runner, 'herdr', self.fake_herdr):
            with self.assertRaisesRegex(RuntimeError, 'timeout'):
                self.loop()
            self.assertEqual(self.state()['status'], 'blocked')
            self.assertEqual(self.state()['pane'], self.pane)
            self.assertFalse(any(c[:2] == ('pane', 'close') for c in self.calls))
            self.args.resume, self.args.feedback = True, 'review interrupted work'
            self.status = 'working'
            with self.assertRaisesRegex(RuntimeError, 'pane'):
                self.loop()
            self.status, self.fail_prompt = 'idle', False
            self.loop()
            self.assertEqual(self.state()['status'], 'complete')

    def test_stale_receipt_is_rejected(self):
        state = dict(run_id='new', phase='implement', task='T-001')
        receipt = dict(run_id='old', phase='implement', task='T-001', action='review', summary='ok')
        with self.assertRaisesRegex(RuntimeError, 'run_id'):
            runner.validate_receipt(receipt, state, self.work)

    def test_interrupted_initial_design_resumes_design_only(self):
        self.args.stop_after = 'design'
        self.fail_prompt = True
        with patch.object(runner, 'herdr', self.fake_herdr):
            with self.assertRaisesRegex(RuntimeError, 'timeout'):
                self.loop()
            self.assertFalse((self.work / 'design.md').exists())
            self.args.resume, self.args.feedback = True, '접근 문제 해결'
            self.fail_prompt = False
            self.loop()
        self.assertEqual(self.phases, ['design'])
        self.assertEqual((self.state()['status'], self.state()['phase']), ('ready', 'implement'))
        self.assertEqual(len([c for c in self.calls if c[:2] == ('agent', 'start')]), 1)

    def test_interrupted_implementation_resumes_review(self):
        self.args.stop_after = 'design'
        with patch.object(runner, 'herdr', self.fake_herdr):
            self.loop()
            self.args.stop_after = 'review'
            self.fail_prompt = True
            with self.assertRaisesRegex(RuntimeError, 'timeout'):
                self.loop()
            interrupted = self.state()
            self.panes.remove(interrupted['pane'])
            self.args.resume, self.args.feedback = True, '구현 pane 종료 확인'
            self.fail_prompt = False
            self.loop()
        self.assertEqual(self.phases, ['design', 'review'])
        # The resumed review must account for the interrupted implementation call.
        self.assertIn(f"run_id: {interrupted['run_id']}", (self.work / 'progress.md').read_text())

    def test_progress_must_match_task_and_implementation_run(self):
        state = dict(run_id='current-run', phase='implement', task='T-002')
        receipt = dict(state, action='review', next_task=None, summary='checked')
        (self.work / 'design.md').write_text('설계 근거')
        (self.work / 'tasks.md').write_text('## T-002\n목표: 두 번째 항목\n')
        cases = [
            'previous task only',
            '## T-001\n### 구현 결과\nrun_id: current-run\n',
            '## T-002\n### 구현 결과\nrun_id: old-run\n',
            '## T-002\n### 구현 결과\nrun_id: old-run\n### 설계 검토\nrun_id: current-run\n',
            '## T-002\n### 구현 결과\nrun_id: old-run\n## T-003\n### 구현 결과\nrun_id: current-run\n',
            '## T-002\n### 구현 결과\nrun_id: current-run-extra\n',
        ]
        for progress in cases:
            with self.subTest(progress=progress):
                (self.work / 'progress.md').write_text(progress)
                with self.assertRaisesRegex(RuntimeError, '현재 작업과 run_id'):
                    runner.validate_receipt(receipt, state, self.work)
        (self.work / 'progress.md').write_text(
            '## T-002\n### 구현 결과\nrun_id: current-run\n검증 통과\n### 설계 검토\n이전 검토\n')
        self.assertEqual(runner.validate_receipt(receipt, state, self.work), 'review')

    def test_review_must_name_the_implementation_it_reviewed(self):
        state = dict(run_id='review-run', phase='review', task='T-001', review_target='impl-run')
        receipt = dict(run_id='review-run', phase='review', task='T-001', action='implement',
                       next_task='T-001', summary='보완 요청')
        (self.work / 'design.md').write_text('설계 근거')
        (self.work / 'tasks.md').write_text(TASKS)
        cases = [
            '## T-001\n### 구현 결과\nrun_id: impl-run\n',
            '## T-001\n### 구현 결과\nrun_id: impl-run\n### 설계 검토\n판정: 통과\n',
            '## T-001\n### 구현 결과\nrun_id: impl-run\n### 설계 검토\nrun_id: 다른-구현\n',
            '## T-001\n### 구현 결과\nrun_id: impl-run\n## T-002\n### 설계 검토\nrun_id: impl-run\n',
        ]
        for progress in cases:
            with self.subTest(progress=progress):
                (self.work / 'progress.md').write_text(progress)
                with self.assertRaisesRegex(RuntimeError, '검토 대상 run_id'):
                    runner.validate_receipt(receipt, state, self.work)
        (self.work / 'progress.md').write_text(
            '## T-001\n### 구현 결과\nrun_id: impl-run\n### 설계 검토\nrun_id: impl-run\n판정: 보완\n')
        self.assertEqual(runner.validate_receipt(receipt, state, self.work), 'implement')

    def test_completion_requires_final_review_section(self):
        state = dict(run_id='review-run', phase='review', task='T-001', review_target='impl-run')
        receipt = dict(run_id='review-run', phase='review', task='T-001', action='complete',
                       next_task=None, summary='완료')
        (self.work / 'design.md').write_text('설계 근거')
        (self.work / 'tasks.md').write_text(TASKS)
        reviewed = '## T-001\n### 구현 결과\nrun_id: impl-run\n### 설계 검토\nrun_id: impl-run\n통과\n'
        (self.work / 'progress.md').write_text(reviewed)
        with self.assertRaisesRegex(RuntimeError, '최종 검토'):
            runner.validate_receipt(receipt, state, self.work)
        (self.work / 'progress.md').write_text(reviewed + '## 최종 검토\nAC-1 근거 확인\n')
        self.assertEqual(runner.validate_receipt(receipt, state, self.work), 'complete')

    def test_next_task_must_exist_in_tasks(self):
        state = dict(run_id='design-run', phase='design', task=None)
        receipt = dict(run_id='design-run', phase='design', task=None, action='implement',
                       next_task='T-007', summary='설계 완료')
        (self.work / 'design.md').write_text('설계 근거')
        (self.work / 'tasks.md').write_text(TASKS)
        with self.assertRaisesRegex(RuntimeError, 'T-007'):
            runner.validate_receipt(receipt, state, self.work)
        (self.work / 'tasks.md').write_text(TASKS + '## T-007\n목표: 추가 항목\n')
        self.assertEqual(runner.validate_receipt(receipt, state, self.work), 'implement')

    def test_crlf_documents_are_accepted(self):
        state = dict(run_id='current-run', phase='implement', task='T-001')
        receipt = dict(state, action='review', next_task=None, summary='checked')
        (self.work / 'design.md').write_text('설계 근거')
        (self.work / 'tasks.md').write_text(TASKS)
        (self.work / 'progress.md').write_text(
            '## T-001\r\n### 구현 결과\r\nrun_id: current-run\r\n검증 통과\r\n')
        self.assertEqual(runner.validate_receipt(receipt, state, self.work), 'review')

    def test_review_without_a_record_stops_the_loop(self):
        self.skip_review_record = True
        with patch.object(runner, 'herdr', self.fake_herdr):
            with self.assertRaisesRegex(RuntimeError, '검토 대상 run_id'):
                self.loop()
        self.assertEqual(self.state()['status'], 'blocked')

    def test_prompt_omits_values_that_do_not_apply(self):
        state = dict(issue='123', phase='design', task=None, run_id='abc',
                     feedback='', review_target=None)
        result = self.work / 'runs' / 'abc.json'
        prompt = runner.prompt_for(state, self.work, self.repo, result)
        self.assertNotIn('None', prompt)
        self.assertNotIn('지정 작업', prompt)
        self.assertNotIn('검토 대상 구현 호출', prompt)
        reviewing = dict(state, phase='review', task='T-001', review_target='impl-run')
        prompt = runner.prompt_for(reviewing, self.work, self.repo, result)
        self.assertIn('지정 작업: T-001', prompt)
        self.assertIn('검토 대상 구현 호출: impl-run', prompt)
        self.assertNotIn('사용자 전달 사항', prompt)
        prompt = runner.prompt_for(dict(reviewing, feedback='pane 확인 완료'),
                                   self.work, self.repo, result)
        self.assertIn('사용자 전달 사항: pane 확인 완료', prompt)

    def log_lines(self):
        return [json.loads(line) for line
                in (self.runtime / 'log.jsonl').read_text().splitlines() if line.strip()]

    def test_run_log_records_every_call(self):
        with patch.object(runner, 'herdr', self.fake_herdr):
            self.loop()
        entries = self.log_lines()
        self.assertEqual([e['phase'] for e in entries], ['design', 'implement', 'review'])
        self.assertEqual([e['action'] for e in entries], ['implement', 'review', 'complete'])
        self.assertEqual(entries[1]['task'], 'T-001')
        self.assertTrue(all(e['run_id'] and e['summary'] and e['at'] for e in entries))

    def test_run_log_records_an_interrupted_call(self):
        self.fail_prompt = True
        with patch.object(runner, 'herdr', self.fake_herdr):
            with self.assertRaisesRegex(RuntimeError, 'timeout'):
                self.loop()
        entries = self.log_lines()
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]['phase'], entries[0]['action']), ('design', 'error'))
        self.assertIn('timeout', entries[0]['summary'])

    def test_blocked_agent_cannot_advance_even_with_receipt(self):
        self.status = 'blocked'
        with patch.object(runner, 'herdr', self.fake_herdr):
            with self.assertRaisesRegex(RuntimeError, '대기 상태'):
                self.loop()
        self.assertFalse(any(c[:2] == ('pane', 'close') for c in self.calls))

    def test_call_limit_resumes_without_repeating_design(self):
        self.args.max_calls = 1
        with patch.object(runner, 'herdr', self.fake_herdr):
            self.loop()
            self.assertEqual(self.state()['phase'], 'implement')
            self.loop()
            self.assertEqual(self.state()['phase'], 'review')

    def test_task_retry_budget_survives_restart(self):
        runner.save(self.state_path, dict(
            repository=str(self.repo), work=str(self.work), issue='123', phase='implement',
            task='T-001', status='ready', pane=None, attempt=5, run_id=None,
            review_target=None, task_attempts={'T-001': 3}))
        with patch.object(runner, 'herdr', self.fake_herdr):
            with self.assertRaisesRegex(RuntimeError, '횟수 제한'):
                self.loop()
        self.assertEqual(self.calls, [])

    def test_retry_budget_counts_reviewed_implementations_only(self):
        self.args.stop_after = 'implement'
        with patch.object(runner, 'herdr', self.fake_herdr):
            self.fail_prompt = False
            self.args.stop_after = 'design'
            self.loop()
            self.args.stop_after = 'implement'
            self.fail_prompt = True
            with self.assertRaisesRegex(RuntimeError, 'timeout'):
                self.loop()
            # An interrupted launch produced nothing, so it must not spend the budget.
            self.assertEqual(self.state()['task_attempts'], {})
            self.panes.remove(self.state()['pane'])
            self.args.resume, self.args.feedback = True, '구현 pane 종료 확인'
            self.fail_prompt, self.continue_review = False, True
            self.loop()
        self.assertEqual(self.state()['task_attempts'], {'T-001': 1})

    def test_design_only_does_not_start_implementation_on_rerun(self):
        self.args.stop_after = 'design'
        with patch.object(runner, 'herdr', self.fake_herdr):
            self.loop()
            self.loop()
        self.assertEqual(self.phases, ['design'])
        self.assertEqual(self.state()['phase'], 'implement')

    def test_implementation_only_then_resume_review(self):
        self.args.stop_after = 'implement'
        with patch.object(runner, 'herdr', self.fake_herdr):
            self.loop()
            self.assertEqual(self.phases, ['design', 'implement'])
            current = self.state()
            self.assertEqual((current['status'], current['phase'], current['pane']),
                             ('ready', 'review', None))
            self.args.stop_after = 'review'
            self.loop()
        self.assertEqual(self.phases, ['design', 'implement', 'review'])

    def test_two_iterations_include_final_review(self):
        self.continue_review = True
        self.args.iterations = 2
        self.args.max_calls = 10
        with patch.object(runner, 'herdr', self.fake_herdr):
            self.loop()
        self.assertEqual(self.phases, ['design', 'implement', 'review', 'implement', 'review'])
        self.assertEqual(self.state()['phase'], 'implement')

    def test_two_implementations_review_only_between_them(self):
        self.continue_review = True
        self.args.iterations = 2
        self.args.stop_after = 'implement'
        with patch.object(runner, 'herdr', self.fake_herdr):
            self.loop()
        self.assertEqual(self.phases, ['design', 'implement', 'review', 'implement'])

    def test_completion_stops_before_requested_iterations(self):
        self.args.iterations = 10
        with patch.object(runner, 'herdr', self.fake_herdr):
            self.loop()
        self.assertEqual(self.phases, ['design', 'implement', 'review'])

    def test_missing_designer_pane_is_recreated(self):
        self.args.stop_after = 'implement'
        with patch.object(runner, 'herdr', self.fake_herdr):
            self.loop()
            previous = self.state()['design_pane']
            self.panes.remove(previous)
            self.args.stop_after = 'review'
            self.loop()
        self.assertNotEqual(self.state()['design_pane'], previous)
        self.assertEqual(self.state()['status'], 'complete')

    def test_design_turns_rotate_the_session(self):
        self.continue_review = True
        self.args.iterations = 2
        self.args.max_calls = 10
        self.args.design_turns = 1
        with patch.object(runner, 'herdr', self.fake_herdr):
            self.loop()
        targets = [c[2] for c in self.calls if c[:2] == ('agent', 'prompt')]
        self.assertEqual(len({targets[0], targets[2], targets[4]}), 3)
        self.assertEqual(self.state()['design_turns'], 1)
        self.assertEqual(len([c for c in self.calls if c[:2] == ('pane', 'close')]), 4)

    def test_repeated_iterations_keep_designer_and_replace_implementers(self):
        self.continue_review = True
        self.args.iterations = 2
        with patch.object(runner, 'herdr', self.fake_herdr):
            self.loop()
        targets = [c[2] for c in self.calls if c[:2] == ('agent', 'prompt')]
        self.assertEqual(targets[0], targets[2])
        self.assertEqual(targets[0], targets[4])
        self.assertNotEqual(targets[1], targets[3])

    def test_unfinished_work_blocks_another_work_folder(self):
        other = self.repo / 'other-work'
        (other / 'runs').mkdir(parents=True)
        with patch.object(runner, 'herdr', self.fake_herdr):
            self.args.stop_after = 'design'
            self.loop()
            with self.assertRaisesRegex(RuntimeError, '미완료 작업'):
                runner.run(self.args, self.repo, other, self.runtime)

    def test_completed_work_allows_another_work_folder(self):
        other = self.repo / 'other-work'
        (other / 'runs').mkdir(parents=True)
        with patch.object(runner, 'herdr', self.fake_herdr):
            self.loop()
            self.assertEqual(self.state()['status'], 'complete')
            self.args.issue = '456'
            self.work = other
            self.loop()
        self.assertEqual(self.state()['work'], str(other))

    def test_missing_work_folder_does_not_deadlock_the_worktree(self):
        other = self.repo / 'other-work'
        (other / 'runs').mkdir(parents=True)
        with patch.object(runner, 'herdr', self.fake_herdr):
            self.args.stop_after = 'design'
            self.loop()
            # Simulate `git clean` removing the work folder: releasing must free the worktree.
            runner.release(self.runtime)
            self.assertFalse(self.state_path.exists())
            self.args.issue = '456'
            self.work = other
            self.args.stop_after = 'review'
            self.loop()
        self.assertEqual(self.state()['work'], str(other))

    def test_release_refuses_live_or_unknown_agents(self):
        for pane, designer, status in [('w1:impl', 'w1:design', 'working'),
                                       ('w1:impl', 'w1:design', 'idle'),
                                       ('w1:design', 'w1:design', 'blocked'),
                                       ('w1:design', 'w1:design', 'working')]:
            with self.subTest(pane=pane, status=status):
                runner.save(self.state_path, dict(work=str(self.work), status='blocked',
                                                 pane=pane, design_pane=designer))
                self.panes = {pane, designer}
                self.status = status
                with patch.object(runner, 'herdr', self.fake_herdr):
                    with self.assertRaisesRegex(RuntimeError, '해제할 수 없습니다'):
                        runner.release(self.runtime)
                self.assertTrue(self.state_path.exists())

    def test_release_checks_designer_without_active_pane(self):
        runner.save(self.state_path, dict(work=str(self.work), status='ready',
                                         pane=None, design_pane='w1:design'))
        self.panes, self.status = {'w1:design'}, 'working'
        with patch.object(runner, 'herdr', self.fake_herdr):
            with self.assertRaisesRegex(RuntimeError, '해제할 수 없습니다'):
                runner.release(self.runtime)
        self.assertTrue(self.state_path.exists())

    def test_release_preserves_state_when_lookup_fails(self):
        runner.save(self.state_path, dict(work=str(self.work), status='blocked', pane='w1:impl'))
        with patch.object(runner, 'herdr', side_effect=RuntimeError('unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                runner.release(self.runtime)
        self.assertTrue(self.state_path.exists())

    def test_release_allows_closed_panes(self):
        runner.save(self.state_path, dict(work=str(self.work), status='blocked',
                                         pane='w1:impl', design_pane='w1:design'))
        with patch.object(runner, 'herdr', self.fake_herdr):
            runner.release(self.runtime)
        self.assertFalse(self.state_path.exists())

    def test_release_refuses_unknown_launch(self):
        runner.save(self.state_path, dict(work=str(self.work), status='launching', pane=None))
        with self.assertRaisesRegex(RuntimeError, 'pane 생성 중'):
            runner.release(self.runtime)
        self.assertTrue(self.state_path.exists())

    def test_prompt_timeout_blocks_loop_and_preserves_pane(self):
        def timed_herdr(*args, **kwargs):
            if args[:2] == ('agent', 'prompt'):
                self.assertEqual(kwargs['timeout'], self.args.timeout + 30)
                raise RuntimeError('명령 시간 초과')
            return self.fake_herdr(*args, **kwargs)
        with patch.object(runner, 'herdr', timed_herdr):
            with self.assertRaisesRegex(RuntimeError, '명령 시간 초과'):
                self.loop()
        self.assertEqual(self.state()['status'], 'blocked')
        self.assertIn(self.state()['pane'], self.panes)
        self.assertEqual(self.log_lines()[0]['action'], 'error')


class CommandTests(unittest.TestCase):
    def test_hung_command_times_out(self):
        with self.assertRaisesRegex(RuntimeError, '명령 시간 초과'):
            runner.command(sys.executable, '-c', 'import time; time.sleep(10)', timeout=0.1)

    def test_herdr_uses_default_and_explicit_deadlines(self):
        with patch.object(runner.subprocess, 'run',
                          return_value=subprocess.CompletedProcess([], 0, '{"result": {}}', '')) as run:
            runner.herdr('agent', 'list')
            self.assertEqual(run.call_args.kwargs['timeout'], 30)
            runner.herdr('agent', 'prompt', timeout=1830)
            self.assertEqual(run.call_args.kwargs['timeout'], 1830)


class EntryPointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.main = root / 'main'
        self.tree = root / 'tree'
        self.git('init', '-q', '-b', 'main', str(self.main))
        self.git('-C', str(self.main), 'config', 'user.email', 'loop@example.com')
        self.git('-C', str(self.main), 'config', 'user.name', 'loop')
        (self.main / 'README').write_text('x')
        self.git('-C', str(self.main), 'add', '.')
        self.git('-C', str(self.main), 'commit', '-qm', 'init')
        self.git('-C', str(self.main), 'worktree', 'add', '-q', '-b', 'loop', str(self.tree))
        self.runtime = self.main / '.git' / 'worktrees' / 'tree' / 'herdr-issue-loop'
        self.recorded = []
        os.environ['HERDR_ENV'] = '1'
        self.addCleanup(os.environ.pop, 'HERDR_ENV', None)

    def git(self, *args):
        subprocess.run(['git'] + list(args), check=True, capture_output=True)

    def entry(self, *argv):
        with patch.object(sys, 'argv', ['runner.py'] + list(argv)), \
                patch.object(runner, 'run', lambda *a: self.recorded.append(a)):
            runner.main()

    def test_worktree_run_reaches_the_loop(self):
        self.entry('123', '--repo', str(self.tree), '--work', 'tmp/issue-123')
        self.assertEqual(len(self.recorded), 1)
        _, repo, work, runtime = self.recorded[0]
        self.assertEqual((repo, work, runtime),
                         (self.tree, self.tree / 'tmp' / 'issue-123', self.runtime))
        self.assertTrue((work / 'runs').is_dir())

    def test_changed_files_counts_new_files_and_excludes_work_documents(self):
        work = self.tree / 'tmp' / 'issue-123'
        work.mkdir(parents=True)
        (work / 'design.md').write_text('design')
        source = self.tree / 'src'
        source.mkdir()
        for index in range(5):
            (source / f'file{index}.py').write_text('x = 1\n')
        (self.tree / 'README').write_text('changed')
        self.assertEqual(runner.changed_files(self.tree, work), 6)

    def test_main_checkout_is_refused(self):
        with self.assertRaises(SystemExit):
            self.entry('123', '--repo', str(self.main), '--work', 'tmp/issue-123')
        self.assertEqual(self.recorded, [])

    def test_work_outside_the_worktree_is_refused(self):
        with self.assertRaises(SystemExit):
            self.entry('123', '--repo', str(self.tree), '--work', str(self.main / 'tmp'))
        self.assertEqual(self.recorded, [])

    def test_run_outside_herdr_is_refused(self):
        os.environ.pop('HERDR_ENV')
        with self.assertRaises(SystemExit):
            self.entry('123', '--repo', str(self.tree), '--work', 'tmp/issue-123')
        self.assertEqual(self.recorded, [])

    def test_invalid_iteration_arguments_are_refused(self):
        for extra in (['--iterations', '0'], ['--max-calls', '0'], ['--design-turns', '-1'],
                      ['--stop-after', 'design', '--iterations', '2']):
            with self.subTest(extra=extra):
                with self.assertRaises(SystemExit):
                    self.entry('123', '--repo', str(self.tree), '--work', 'tmp/issue-123', *extra)
        self.assertEqual(self.recorded, [])

    def test_second_runner_on_the_same_worktree_is_refused(self):
        self.runtime.mkdir(parents=True, exist_ok=True)
        with (self.runtime / 'lock').open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, '이미 동작 중'):
                self.entry('123', '--repo', str(self.tree), '--work', 'tmp/issue-123')
        self.assertEqual(self.recorded, [])

    def test_release_runs_outside_herdr_and_needs_no_issue(self):
        os.environ.pop('HERDR_ENV')
        self.runtime.mkdir(parents=True, exist_ok=True)
        runner.save(self.runtime / 'state.json',
                    {'work': str(self.tree / 'tmp'), 'status': 'blocked'})
        self.entry('--repo', str(self.tree), '--release')
        self.assertFalse((self.runtime / 'state.json').exists())
        self.assertEqual(self.recorded, [])

    def test_release_rejects_a_work_argument(self):
        with self.assertRaises(SystemExit):
            self.entry('--repo', str(self.tree), '--release', '--work', 'tmp/issue-123')


if __name__ == '__main__':
    unittest.main()
