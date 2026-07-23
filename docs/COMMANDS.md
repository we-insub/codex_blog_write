# 개발자·내부 문제 해결 명령

[README로 돌아가기](../README.md)

> 이 문서는 플러그인 개발자와 장애 진단을 위한 내부 명령 모음입니다. Mato Blog Codex는 독립 실행 CLI 제품이 아닙니다. 일반 사용자는 이 명령을 직접 실행하지 말고 Codex 데스크톱 앱 또는 모바일 Remote에서 한국어로 요청하십시오.

일반 사용자 시작 예시는 다음 한 줄입니다.

```text
"서울 맛집" 1번 프로필, 블로그탭
```

이 요청은 기본값 `통합검색 + 10개 + 임시저장`으로 처리됩니다. 생성 수를 명시하면 정확히 그 수만큼 만들며, `발행`, `바로발행`, `자동발행`을 명시하면 같은 요청에서 공개 발행까지 진행합니다.

아래 명령은 Codex 스킬이 내부에서 실행하거나 개발자가 상태를 진단할 때 사용합니다. 터미널 옵션의 검색 영역과 발행 방식은 영문 값을 사용합니다.

아래 명령은 모두 저장소 루트에서 실행합니다.

```powershell
cd C:\path\to\mato-blog-codex
```

## 실행 환경 준비

```powershell
py plugins/mato-blog-codex/scripts/bootstrap.py
$MatoPython = "$env:USERPROFILE\.googleblog\mato-blog-codex\.venv\Scripts\python.exe"
```

PC별 전용 가상환경과 필요한 패키지를 준비합니다. 저장소를 처음 설치했거나 의존성이 변경된 뒤 한 번 실행합니다. 이후 명령은 의존성이 설치된 `$MatoPython`으로 실행합니다.

## 프로필

기존 `naver_N` 폴더를 찾아 프로필 목록에 추가합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/profiles.py discover
```

등록된 프로필 번호, 별칭, 블로그 URL, 로그인 확인 상태를 표시합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/profiles.py list
```

프로필 슬롯을 등록합니다. 비밀번호는 옵션으로 전달하지 않습니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/profiles.py add --slot 1 --alias "업무용" --blog-url "https://blog.naver.com/example"
```

`--blog-url`에는 `example`, `https://blog.naver.com/example`, `https://m.blog.naver.com/example` 중 하나를 사용할 수 있습니다. 저장할 때 블로그 URL은 `https://blog.naver.com/example`, 글쓰기 URL은 `https://blog.naver.com/example?Redirect=Write&`로 자동 정규화됩니다.

여러 프로필은 슬롯별로 반복 등록합니다. 코드상 슬롯 최대 개수는 없지만 각 슬롯은 1 이상의 고유 정수여야 합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/profiles.py add --slot 1 --alias "intp_kr" --blog-url "intp_kr"
& $MatoPython plugins/mato-blog-codex/scripts/profiles.py add --slot 2 --alias "youtube_intp" --blog-url "youtube_intp"
```

별칭 또는 블로그 URL을 수정합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/profiles.py edit --slot 1 --alias "업무용 메인" --blog-url "https://blog.naver.com/example"
```

프로필 폴더가 존재하는지만 안전하게 검사하려면 다음 명령을 사용합니다. 이 명령은 Chrome을 열지 않습니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/profiles.py check --slot 1
```

표시되는 Chrome에서 직접 로그인하고 글쓰기 화면 접근 여부까지 확인하려면 `--login`을 명시해야 합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/profiles.py check --slot 1 --login
```

선택한 전용 Chrome 창을 loopback 연결기와 함께 열어두려면 `open`을 사용합니다. 이후 `check --login`과 `upload.py --execute`는 가능한 경우 이 창에 재연결합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/profiles.py open --slots 1,2
```

`open`은 종료 명령이 아니라 창 유지 명령입니다. 업로드가 시작된 뒤 성공 URL 확인이 끝날 때까지 해당 창을 사용자가 닫거나 다른 URL로 이동하지 않아야 합니다. 창을 닫아도 디스크의 로그인 세션이 자동 삭제되지는 않지만 현재 업로드 연결은 끊깁니다.

같은 `naver_N` 폴더가 연결기 없이 이미 열려 있으면 두 번째 Chrome을 강제로 실행하지 않고 프로필 잠금 오류를 냅니다. 해당 전용 창에 작성 중인 글이 없는지 확인하고 정상 종료한 뒤 `open`을 다시 실행합니다.

각 네이버 계정에는 이름이 `제목을입력해주세요1:`인 내 템플릿과 `본문2:` 텍스트 블록을 준비하는 것을 권장합니다. 네이버 목록에서 마지막 콜론이 생략되어 보여도 같은 템플릿으로 인식합니다. `본문1:`·`인트로1:`이 있는 원고는 같은 자리도 필요합니다. 업로더는 제목과 각 영역을 분리하고 본문·표 셀을 20ms 고정 지연으로 실제 타이핑합니다. 템플릿이 없으면 기본 편집기 자리로 폴백하지만 원고 전체 붙여넣기나 DOM 삽입은 사용하지 않습니다. URL 한 줄만 링크카드 생성을 위해 클립보드 붙여넣기를 사용합니다.

선택한 로컬 TXT 폴더를 Mato `_함축.txt` 형식으로 변환하려면, Codex에 “이 폴더를 글변환해줘”라고 요청합니다. 내부 도구를 직접 점검해야 하는 경우에는 행 배열 JSON을 전달합니다. 기존 결과 파일을 바꾸려면 `--overwrite`를 명시해야 합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/convert.py --input .\convert-rows.json
```

원고 10개를 프로필 `1,2,3`에 어떻게 배정할지 미리 봅니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/profiles.py assign --profiles 1,2,3 --count 10
```

배정은 입력한 프로필 순서대로 반복됩니다. 위 예시에서는 원고 1→1, 2→2, 3→3, 4→1 순서입니다.

## 인앱 Browser 실행 준비와 수집

정상 워크플로는 요청 원문과 Browser 결과를 Git 저장소 밖 `~/.googleblog/mato-blog-codex/staging/<임시-ID>/`에 잠시 둡니다. 먼저 요청을 해석하고, Browser를 열기 전에 실행과 검색 시작 시각을 기록합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/parse_request.py --command-file "<로컬-staging>\request.txt"
& $MatoPython plugins/mato-blog-codex/scripts/ingest_browser_sources.py --prepare --keyword "서울 맛집" --surface blog --versions 1 --command-file "<로컬-staging>\request.txt"
```

위 명령이 출력한 실행 폴더를 보존합니다. Codex가 `@Browser`에서 공개 글을 확인해 로컬 staging JSON을 만든 뒤 같은 실행에 연결합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/ingest_browser_sources.py --input "<로컬-staging>\browser-sources.json" --keyword "서울 맛집" --surface blog --versions 1 --run-dir "runs\<실행-ID>"
```

staging 파일은 각 전달이 끝나면 삭제합니다. 경쟁 글 원문을 저장소 안에 두지 않습니다.

## 진단용 보조 수집기

`collect.py`는 Browser 장애를 조사하는 개발자용 보조 수집기입니다. 일반 자연어 작업에서 `@Browser`를 몰래 대체하지 않습니다. 블로그탭을 진단하고 생성 목표를 10개로 기록하는 예시는 다음과 같습니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/collect.py --keyword "서울 맛집" --surface blog --versions 10
```

통합검색은 `integrated`를 사용합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/collect.py --keyword "제주 렌터카" --surface integrated --versions 3
```

원하는 실행 폴더와 사용자의 원래 명령을 함께 기록할 수 있습니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/collect.py --keyword "서울 맛집" --surface blog --versions 10 --run-dir "runs\seoul-food" --command "서울 맛집 블로그탭 원고 10개 만들어줘"
```

| 옵션 | 허용 값 | 의미 |
| --- | --- | --- |
| `--keyword` | 문자열 | 필수 검색어 |
| `--surface` | `blog`, `integrated` | 블로그탭 또는 통합검색 |
| `--versions` | 1 이상의 정수 | Codex가 생성할 목표 원고 수, 생략 시 10 |
| `--run-dir` | 로컬 경로 | 생략 시 `바탕화면/YYYY-MM-DD/` 아래 자동 생성 |
| `--command` | 문자열 | 히스토리에 남길 사용자 원문 명령 |

`collect.py`는 최대 5개의 검색 자료와 실행 상태를 준비합니다. 별도 AI API를 호출해 글을 만들지는 않습니다. 현재 Codex가 자료를 분석하고 실행 폴더의 `posts/`에 요청 수만큼 원고를 작성합니다.

## 원고 검증

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/validate_posts.py --run-dir "runs\<실행-ID>" --expected 10
```

검증기는 파일 수, 제목·본문 표식, 소제목, 빈 본문, 중복 제목과 원고 간 과도한 유사성을 확인합니다. 실패하면 업로드 전에 원고를 수정하거나 Codex에 재생성을 요청합니다.

## 업로드 계획 확인

임시저장 계획을 출력합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/upload.py --run-dir "runs\<실행-ID>" --profiles 1,2,3 --mode draft
```

자동발행 계획은 `publish`를 사용합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/upload.py --run-dir "runs\<실행-ID>" --profiles 1,2,3 --mode publish
```

두 명령 모두 기본 상태에서는 네이버에 쓰지 않습니다. 프로필 별칭·블로그 URL·원고 제목·라운드로빈 배정표와 `RUN_ID`만 출력합니다.

## 내부 서명 확인 후 실제 실행

업로더는 계획에 저장된 `RUN_ID`를 그대로 받아야 합니다. Codex 자연어 워크플로에서는 이 값을 내부에서 전달하며 사용자에게 다시 입력하도록 요청하지 않습니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/upload.py --run-dir "runs\<실행-ID>" --profiles 1,2,3 --mode draft --execute --confirm "<RUN_ID>"
```

자동발행 예시입니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/upload.py --run-dir "runs\<실행-ID>" --profiles 1,2,3 --mode publish --execute --confirm "<RUN_ID>"
```

`--execute`와 일치하는 `--confirm` 값 중 하나라도 없으면 외부 쓰기를 시작하지 않습니다. 이 옵션은 사용자 재승인 절차가 아니라 변경된 파일·대상을 차단하는 내부 안전 서명입니다. 실패 후 같은 실행 폴더로 다시 실행하면 `run.json`에서 이미 성공한 원고를 확인하고 미완료 항목부터 처리합니다.

## 불확실한 업로드 결과 확인

버튼을 눌렀지만 성공 상태를 확실히 확인하지 못한 항목은 `uncertain`으로 멈춥니다. 사용자가 네이버 관리 화면에서 해당 원고를 직접 확인한 뒤, 히스토리에 표시된 정확한 원고 번호·엔트리 키·실행 ID로 상태를 해소합니다.

실제로 저장되지 않았음을 확인해 다시 시도할 수 있게 만드는 예시입니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/upload.py --run-dir "runs\<실행-ID>" --resolve-uncertain 2 --entry-key "<정확한-entry-key>" --resolution not-uploaded --confirm "<RUN_ID>"
```

공개 발행 성공을 수동 확인한 경우에는 해당 글의 직접 URL도 필요합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/upload.py --run-dir "runs\<실행-ID>" --resolve-uncertain 2 --entry-key "<정확한-entry-key>" --resolution success --verified-url "https://blog.naver.com/example/123456" --confirm "<RUN_ID>"
```

이 명령은 네이버를 조작하지 않고 로컬 재개 상태와 히스토리만 갱신합니다. 확인하지 않은 결과를 추측해 성공으로 바꾸지 않습니다.

## 히스토리 보기

이 PC의 전체 실행 히스토리를 표시합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/history.py show
```

특정 실행의 히스토리만 표시합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/history.py show --run-dir "runs\<실행-ID>"
```

`history.py event`는 수집·생성·검증·업로드 스크립트와 Codex 워크플로가 단계별 이벤트를 추가할 때 사용하는 내부용 명령입니다. 정확한 이벤트 옵션은 설치된 버전에서 확인합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/history.py event --help
```

히스토리의 항목과 민감정보 처리 방식은 [히스토리 기록 형식](HISTORY_FORMAT.md)을 참고하십시오.

## 자연어 요청 예시

```text
프로필 1을 업무용으로 등록하고 블로그 URL은 https://blog.naver.com/example 로 설정해줘.
```

```text
서울 맛집을 통합검색에서 찾아줘. 보이는 블로그 글만 분석해서 원고 5개를 만들어줘.
```

```text
방금 만든 실행을 검사하고 프로필 1,2에 배정표만 보여줘. 아직 업로드하지 마.
```

```text
배정표를 확인했어. 해당 실행을 임시저장으로 진행해줘.
```

```text
중단된 실행의 히스토리를 보여주고 완료되지 않은 글부터 재개해줘.
```

모바일에서 같은 요청을 보내는 방법은 [모바일 Remote 사용 안내](MOBILE_REMOTE.md)를 참고하십시오.

## 공통 프롬프트 관리

최초 한 번 플러그인 기본 `공통` 프롬프트를 PC별 편집 파일로 만듭니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/prompt_manager.py init --prompt-key "공통"
```

편집할 파일은 `~/.googleblog/mato-blog-codex/prompts/공통.txt`입니다. 이 파일을 수정하면 다음 원고 생성부터 수정본을 사용합니다. 현재 경로와 해시는 다음 명령으로 확인합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/prompt_manager.py status --prompt-key "공통"
```

Codex는 매 실행마다 관리 파일에 오늘 날짜 컨텍스트를 붙여 임시 프롬프트를 내보내고, 그 키와 SHA-256을 생성 기록에 남깁니다.

## 본인 소유 이미지 메타데이터 정리

URL 다운로드로 유지된 이미지는 기본적으로 자동 정리됩니다. 네이버와 무관한 본인 소유 또는 사용 허가 이미지 파일·폴더를 직접 처리해야 할 때만 아래 내부 명령을 사용합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/sanitize_images.py "C:\path\to\owned-images" --recursive --manifest "C:\path\to\owned-images\image-processing.json"
```

처리 과정은 EXIF 방향 적용, GPS·EXIF·XMP·ICC 제거, 동일 파일명 재인코딩 순서입니다. 처리 내역에는 메타데이터 실제 값이 아니라 제거 전 존재 여부와 처리 전후 SHA-256만 남습니다.

## 원고별 내 이미지 데이터셋

내 글 URL에서 내려받은 이미지 폴더를 원고마다 별도 데이터셋으로 만들려면, 원고 생성 후 다음 내부 명령을 사용합니다.

```powershell
& $MatoPython plugins/mato-blog-codex/scripts/prepare_image_datasets.py --run-dir "<실행 폴더>" --source-dir "<실행 폴더>\sources\items\<내-글-폴더>"
```

원고가 N개면 원본 이미지 데이터셋은 1회 정리되고, N개 원고 폴더에 각각 이미지가 복사된 뒤 N회 정리됩니다. 각 원고에 `[image_N.jpg]` 태그가 없으면 같은 순서로 추가한 뒤, 마지막으로 원고 검증을 다시 실행합니다.
