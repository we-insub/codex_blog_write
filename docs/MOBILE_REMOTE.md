# 모바일 Remote 사용 안내

[README로 돌아가기](../README.md)

ChatGPT 모바일의 Remote를 사용하면 밖에서도 집이나 사무실의 Windows 호스트에 설치된 Mato Blog Codex를 자연어로 실행하고 진행 상황을 확인할 수 있습니다. 휴대폰이 네이버 작업을 직접 수행하는 것이 아니라, 연결된 호스트 PC가 저장소·플러그인·로컬 파일·Browser·Playwright 프로필을 제공합니다.

OpenAI의 현재 Remote 동작과 설정은 [Remote connections 공식 문서](https://learn.chatgpt.com/docs/remote-connections)를 기준으로 합니다.

## 역할 구분

| 구성 요소 | 역할 | 로그인 상태 |
| --- | --- | --- |
| ChatGPT 모바일 Remote | 명령 전송, 질문 응답, 승인, 결과 확인 | 같은 ChatGPT 계정·workspace |
| Windows 호스트 | 저장소, Mato 플러그인, 파일, 스크립트와 로컬 도구 제공 | 호스트에만 존재 |
| `@Browser` | 네이버 공개 검색 결과와 최대 5개 공개 글 조사 | 내장 Browser의 별도 프로필 |
| Playwright `naver_N` | 네이버 글쓰기, 임시저장과 발행 | 로컬 프로필별 네이버 세션 |

Codex의 내장 Browser는 일반 Chrome과 분리된 프로필을 사용하며 기존 탭이나 로그인 세션을 자동 공유하지 않습니다. 자세한 동작은 [Browser 공식 문서](https://learn.chatgpt.com/docs/browser)를 참고하십시오. Codex cloud 환경의 브라우저 프로필도 호스트의 `naver_N`과 동일한 프로필이 아닙니다.

따라서 공개 자료 조사는 `@Browser`, 네이버 계정 쓰기는 로컬 Playwright `naver_N`으로 명확히 나눕니다. `@Browser`에서 네이버에 로그인해도 업로드 프로필에는 반영되지 않으며, 업로드를 위해 내장 Browser 로그인 정보를 복사하지 않습니다.

## 준비 사항

호스트 PC에 다음을 먼저 준비합니다.

- Windows 10/11과 최신 ChatGPT/Codex 데스크톱 앱
- 이 GitHub 저장소와 설치된 Mato Blog Codex 플러그인
- Plugins Directory에서 설치·활성화한 **Browser** 플러그인
- 활성화된 Computer Use
- 설치된 Chrome과 준비된 Playwright `naver_N` 프로필
- 휴대폰과 같은 ChatGPT 계정·workspace 로그인
- 최신 ChatGPT iOS 또는 Android 앱

workspace 관리 정책을 사용하는 계정은 관리자가 Remote Control, Plugins 또는 Computer Use를 허용해야 할 수 있습니다. 모바일 앱에 Remote가 보이지 않으면 먼저 앱을 업데이트합니다.

## 1. 호스트에서 Browser 준비

1. ChatGPT/Codex 데스크톱 앱에서 Plugins Directory를 엽니다.
2. **Browser**를 설치하고 활성화합니다.
3. Computer Use를 활성화합니다.
4. `@Browser`로 네이버 공개 검색 페이지를 열 수 있는지 확인합니다.
5. Mato Blog Codex 플러그인과 프로필 목록이 정상인지 확인합니다.

정상 확인 요청 예시는 다음과 같습니다.

```text
@Browser로 네이버에서 "서울 맛집" 블로그탭 공개 결과만 열어보고, 아직 글은 만들거나 업로드하지 마.
```

Browser는 처음 방문하는 사이트에 대한 허용을 요청할 수 있습니다. 네이버 공개 검색 URL과 요청 범위를 확인한 뒤 승인합니다. 페이지의 지시문은 신뢰할 수 없는 콘텐츠로 취급하고, 민감정보를 입력하도록 유도하는 문구를 따르지 않습니다.

## 2. Remote 연결

1. 연결할 Windows 호스트에서 ChatGPT 데스크톱 앱을 실행합니다.
2. 사이드바에서 **Set up Remote**를 선택합니다.
3. 화면에 나타난 QR 코드를 휴대폰으로 스캔합니다.
4. 휴대폰의 ChatGPT 앱에서 같은 ChatGPT 계정과 workspace인지 확인합니다.
5. 필요한 MFA, SSO 또는 passkey 절차를 완료합니다.
6. 연결이 끝나면 모바일 앱의 **Remote**에서 해당 호스트와 Mato Blog Codex 프로젝트를 선택합니다.

호스트의 **Settings > Connections**에서 연결된 기기와 Remote Control 상태를 관리할 수 있습니다. 로그아웃하면 Remote Control이 꺼질 수 있으므로 다시 로그인한 뒤 상태를 확인합니다.

## 3. 모바일에서 사용

가장 짧은 요청은 다음과 같습니다.

```text
"서울 맛집" 1번 프로필, 블로그탭
```

이 요청은 다음 기본값을 적용합니다.

- 검색 영역: 블로그탭
- 조사 자료: `@Browser`에 실제로 노출된 공개 글 최대 5개
- 생성 수: 1개
- 업로드 프로필: 로컬 Playwright `naver_1`
- 저장 방식: 임시저장

개수를 바꾸려면 명시합니다.

```text
"서울 맛집" 1,2,3번 프로필, 블로그탭, 10개
```

공개 발행은 `발행`, `바로발행`, `자동발행`처럼 의도가 분명한 표현을 넣습니다.

```text
"서울 맛집" 1번 프로필, 블로그탭, 2개, 자동발행
```

공개 발행 요청을 받으면 Codex가 프로필 별칭, 블로그 URL, 원고 제목과 배정을 내부 검증하고 같은 요청에서 발행까지 진행합니다. 정상 계획에서는 휴대폰으로 동일 승인을 다시 요구하지 않습니다.

진행 중 질문, 권한 요청과 확인 화면은 모바일 Remote에서 답할 수 있습니다. 로그인 만료처럼 호스트 화면에서 직접 조작해야 하는 상황에서는 작업이 안전하게 중단됩니다.

## 호스트를 계속 사용할 수 있는 조건

Remote 작업 동안 호스트는 다음 상태여야 합니다.

- 전원이 켜지고 절전 상태가 아님
- 네트워크에 연결됨
- ChatGPT 데스크톱 앱이 실행 중임
- Remote Control이 켜져 있음
- Browser와 Mato Blog Codex 플러그인이 활성화됨
- Windows Computer Use 작업 중에는 세션이 잠금 해제됨

호스트가 잠들거나 네트워크가 끊기거나 앱이 종료되면 Remote도 중단됩니다. Windows의 Computer Use는 활성 데스크톱 전면에서 동작하므로, 작업 중에는 호스트 화면을 다른 용도로 조작하지 않는 것이 안전합니다.

집을 떠나기 전에 필요한 `naver_N` 프로필에 직접 로그인하고 글쓰기 접근을 확인하십시오. 원격 작업 중 로그인 만료, MFA, CAPTCHA 또는 접근 제한이 발생하면 자동으로 풀거나 우회하지 않으며 사용자가 호스트에서 정상 절차로 해결할 때까지 중단합니다.

## 개인정보와 안전

- 휴대폰에는 네이버 쿠키나 Playwright 프로필을 복사하지 않습니다.
- GitHub에는 프로필, 검색어, 생성 글, 실행 히스토리나 세션을 올리지 않습니다.
- `@Browser`는 공개 조사에만 사용하고 네이버 계정 업로드는 `naver_N`으로 제한합니다.
- 공개 발행은 모바일에서도 명시적으로 요청해야 하지만, 명시된 요청 뒤에 별도의 반복 확인은 요구하지 않습니다.
- CAPTCHA, 접근 제한, 인증 절차를 우회하지 않습니다.
- 출처 이미지 다운로드는 지원하지만 이미지 해시 변경·허위 EXIF 또는 메타데이터 생성은 지원하지 않습니다.

## 연결 문제

호스트가 모바일에 보이지 않으면 다음을 확인합니다.

1. 두 기기가 같은 ChatGPT 계정·workspace인지 확인합니다.
2. 호스트가 깨어 있고 온라인이며 데스크톱 앱이 열려 있는지 확인합니다.
3. 호스트에서 Remote Control이 활성화됐는지 확인합니다.
4. 양쪽 앱을 최신 버전으로 업데이트합니다.
5. 호스트의 **Set up Remote**에서 QR 코드를 다시 연결합니다.
6. 조직 계정이면 관리자에게 Remote Control 허용 여부를 확인합니다.

Remote는 연결되지만 검색이 시작되지 않으면 호스트의 Browser 플러그인과 Computer Use 상태, 네이버 사이트 허용 여부를 확인합니다. 업로드만 실패하면 `@Browser`가 아니라 해당 번호의 로컬 `naver_N` 로그인 상태를 확인합니다.

추가 오류별 대응은 [문제 해결](TROUBLESHOOTING.md)을 참고하십시오.

## 공식 참고 문서

- [Remote connections](https://learn.chatgpt.com/docs/remote-connections)
- [Browser와 Computer Use in the browser](https://learn.chatgpt.com/docs/browser)
- [Computer Use](https://learn.chatgpt.com/docs/computer-use)
