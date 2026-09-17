# 구현 항목

- 상태 값: `pending`, `in_progress`, `blocked`, `done`, `cancelled`

## T-001

- 목표: `resolve_log_path()` 추가
- 설계: 구성 요소의 `checker/logging.py`, 동작과 오류 처리 1~3
- 수용 조건: AC-1, AC-3
- 의존: 없음
- 검증: `pytest tests/test_logging.py`. 미설정, 정상 경로, 쓸 수 없는 경로 세 경우 확인
- 상태: done

## T-002

- 목표: `setup_logging()`에 연결하고 사용법 문서화
- 설계: 구성 요소의 `setup_logging()` 수정
- 수용 조건: AC-2
- 의존: T-001
- 검증: `pytest tests/test_cli.py`, README의 환경 변수 설명 확인
- 상태: done
