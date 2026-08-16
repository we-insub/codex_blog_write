# Mato Blog Codex 작업 안내

이 저장소는 Mato Blog Codex 플러그인 프로젝트입니다.

## 모든 작업 전에 확인

1. 먼저 `README.md`를 읽어 설치와 운영 방식을 확인합니다.
2. 네이버 원고 생성, 프로필, 업로드, 마이리얼트립 상품 관련 요청이면 반드시 `plugins/mato-blog-codex/skills/naver-blog-workflow/SKILL.md`를 처음부터 끝까지 읽고 그 규칙을 따릅니다.
3. `SKILL.md`가 연결한 참고 문서는 해당 작업에 필요한 범위에서 읽습니다.

## 다른 PC에서 플러그인 활성화

저장소를 복제한 것만으로는 Codex에 작업 규칙이 자동 설치되지 않습니다. 저장소 최상위 폴더에서 아래 두 명령을 실행해 개인 마켓플레이스와 플러그인을 등록합니다.

```shell
codex plugin marketplace add "$PWD"
codex plugin add mato-blog-codex@personal
```

설치 뒤 Codex를 재시작합니다. 이후에는 자연어로 요청해도 `naver-blog-workflow` 지침이 적용됩니다.

## 공유 범위

- Git에는 플러그인 코드와 문서만 공유합니다.
- 네이버 로그인 세션, 쿠키, 비밀번호, 실행 원고와 실행 기록은 각 PC의 로컬 저장소에만 둡니다.
- 각 PC는 번호별 네이버 Chrome 프로필을 직접 만들고 로그인해야 합니다.
