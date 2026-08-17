# Atlas Product Requirements Document

**문서 상태:** Draft v0.2
**작성일:** 2026-08-17
**대상:** 제품·디자인·엔지니어링·AI/ML·보안
**제품 유형:** Agent용 SKILL 모음 + artifact 계약 + self-contained HTML 뷰어 템플릿

**v0.1 대비 변경 요약:** 제품 정체성을 "로컬 우선 repository intelligence 앱"에서 "agent가 로드하는 SKILL 중심 도구"로 전환한다. 자체 agent runtime과 전용 Web viewer 앱 계획을 제거하고, JSON artifact + 버전 관리되는 HTML 뷰어 템플릿 + skill 내장 scripts 조합으로 재정의한다. 사람의 인터페이스는 agent와의 대화와 HTML artifact 둘로 좁힌다. Slice 기반 code navigation과 incremental update를 1급 요구사항으로 추가한다.

---

## 1. 제품 요약

Atlas는 **agent(예: Claude Code)가 사용하는 SKILL 모음**이다. Agent는 SKILL의 조사 절차에 따라 IDE 없이 코드베이스를 분석하고, 그 결과를 검증 가능한 JSON artifact로 산출한다. skill에 내장된 scripts가 이 데이터를 버전 관리되는 뷰어 템플릿과 결합해 **self-contained HTML artifact**를 생성하고, 사용자는 브라우저만으로 코드 구조를 탐색한다. 사람이 배워야 할 명령은 없다 — 사람의 인터페이스는 agent와의 대화, 그리고 HTML artifact 둘뿐이다.

두 가지 모드를 하나의 evidence system 위에서 제공한다.

1. **Codebase Learning Mode** — architecture, 실행 경로, domain concept, invariant, failure/recovery path를 학습한다. HTML artifact 안에서 go-to-definition, go-to-references 수준의 symbol 탐색이 가능하다.
2. **Diff Review Mode** — 변경 의도와 실제 구현의 차이, semantic impact, invariant 위반, 테스트 누락, 운영 위험을 검토한다.

제품의 핵심 자산은 네 가지의 결합이다.

```text
SKILL          — 조사 절차, 권한, 출력 규약 (agent가 로드)
Artifact Schema — 기계 판독 가능한 JSON 계약
Viewer Template — 버전 관리되는 self-contained HTML 렌더러
Scripts        — skill 내장 결정적 도구: symbol indexer, renderer, validator
```

네 요소는 하나의 skill 패키지로 배포되어 버전이 함께 잠긴다. 별도 설치물은 없다.

Atlas는 특정 IDE에도, 특정 agent 제품에도 종속되지 않는다. SKILL 계약을 따르는 어떤 agent든 artifact를 생성할 수 있고, 어떤 브라우저·터미널·Vim에서든 결과를 소비할 수 있다.

---

## 2. 문제 정의

### 2.1 코드베이스 학습 문제

새 코드베이스를 학습할 때 README, 파일 트리, AI 요약과 architecture diagram만 읽으면 이해한 느낌은 들지만 다음 능력을 확보하기 어렵다.

- 요청·event가 실제로 어떤 symbol 경로를 거치는지 재구성
- domain concept 간 차이 설명
- 변경을 구현할 올바른 위치 선택
- 지켜야 할 invariant와 검증 test 식별
- 정상 경로뿐 아니라 실패·복구 경로 이해
- 현재 구조가 생긴 역사적 이유와 미확인 추측 구분

또한 기존 도구들은 다음 한계가 있다.

- 깊은 탐색(go to definition, find references)이 IDE에 묶여 있어, 요약 문서와 실제 코드 탐색이 분리된다.
- AI가 생성한 설명은 주석 스타일 산문에 그쳐 구조(그래프, 흐름, 계층)를 시각적으로 전달하지 못한다.
- 분석 대상 코드가 갱신되면(예: main 최신화) 문서 전체가 stale해지고, 무엇이 바뀌었는지 파악하려면 처음부터 다시 분석해야 한다.

### 2.2 코드리뷰 문제

AI coding agent의 도입으로 코드 생산량은 증가하지만 인간의 리뷰 시간과 저장소 이해 속도는 같은 비율로 증가하지 않는다. 현재 AI 코드리뷰 도구는 다음 문제가 있다.

- diff line만 보고 caller·callee, schema, 상태 전이와 운영 영향을 놓친다.
- 낮은 precision으로 많은 댓글을 생성해 리뷰어의 주의를 소모한다.
- PR 의도를 사실로 가정하거나, 반대로 의도를 모른 채 일반적인 스타일 조언을 한다.
- 테스트 통과와 coverage를 correctness 증거로 과대평가한다.
- 지적에 대한 재현 방법, 반증 방법과 provenance가 부족하다.

### 2.3 공통 원인

Review와 learning은 별개 문제가 아니다.

- Learning은 "기존 구조와 invariant는 무엇이며 왜 존재하는가?"를 묻는다.
- Review는 "이 변경이 기존 구조와 invariant에 어떤 영향을 주는가?"를 묻는다.

두 기능이 동일한 repository evidence를 공유하지 않으면 분석 비용이 중복되고, review에서 발견한 지식이 onboarding에 남지 않으며, onboarding 설명은 실제 변경과 분리되어 빠르게 stale해진다.

---

## 3. 제품 비전

> Atlas는 agent에게 코드베이스를 조사하는 절차(SKILL)를 제공하고, 그 결과를 사람이 브라우저 하나로 깊이 탐색할 수 있는 검증 가능한 artifact로 만든다. IDE 없이도 이해 속도를 극대화하고, 코드가 바뀌면 바뀐 부분만 따라잡는다.

장기적으로 repository knowledge가 review, onboarding, incident analysis와 변경 설계에 재사용되는 repository intelligence layer가 되는 것을 목표로 한다.

---

## 4. 핵심 원칙

1. **Evidence before confidence**
   신뢰도 숫자보다 file, symbol, commit, test와 실제 실행 결과를 먼저 제시한다.

2. **Data over freestyle**
   Agent는 매 run마다 HTML을 자유 생성하지 않는다. Agent는 schema를 따르는 JSON 데이터만 산출하고, 렌더링은 버전 관리되는 뷰어 템플릿이 담당한다. UI 품질은 템플릿 버전으로 축적되고, artifact는 재렌더링 가능하다.

3. **Artifact is the contract**
   Artifact(JSON)와 뷰어(HTML)를 분리한다. HTML 파일은 export 산출물이지 저장 원본이 아니다.

4. **Precision first for findings, depth first for explanation**
   Finding은 댓글 수나 recall을 최대화하지 않는다. 실제로 수정하거나 사람이 판단할 가치가 있는 finding만 게시한다. 단, 이 예산은 finding 게시에만 적용된다. 코드를 읽고 이해하는 데 필요한 로직 흐름 설명은 수정·판단 대상이 아니어도 상세하게 제공한다 — 절약 대상은 지적의 개수이지 설명의 깊이가 아니다.

5. **Semantic analysis**
   line-by-line이 아니라 symbol, call path, data flow, state transition, API/schema contract와 invariant를 분석한다.

6. **Independent verification**
   구현 agent가 테스트와 완료 판정을 독점하지 않는다. 결정적 도구, 별도 verifier, 사람이 정의한 acceptance criteria를 결합한다.

7. **Active learning**
   architecture 설명을 보여주는 것으로 끝내지 않는다. 예측, 자기 설명, 회상, 적용 문제, feedback과 간격 복습을 제공한다.

8. **Editor and runtime agnostic**
   특정 IDE도, 특정 agent 제품도 전제로 삼지 않는다. SKILL 계약·내장 scripts·표준 path/range·normalized symbol protocol을 기본 인터페이스로 사용한다.

9. **Immutable and auditable runs**
   각 실행의 입력 revision, skill/template/schema version, model/tool version, evidence를 추적한다.

10. **Human-owned oracle**
    사람은 specification, 핵심 invariant, test oracle, 위험 모델과 승인 기준을 소유한다.

11. **Local-first and least privilege**
    소스와 민감 데이터는 가능한 로컬에 유지한다. SKILL은 read-only 조사를 기본으로 하고 최소 도구 권한만 요구한다.

---

## 5. 목표와 비목표

### 5.1 제품 목표

- IDE를 열지 않고, 생성된 HTML artifact만으로 낯선 subsystem의 구조·실행 경로·invariant를 탐색할 수 있게 한다.
- artifact 안에서 symbol 클릭 → definition/reference 이동이 가능한 slice 범위 navigation을 제공한다.
- 주석 스타일 산문이 아니라 call graph, execution flow, 계층 트리 등 interactive figure로 구조를 전달한다.
- 분석 대상 revision이 바뀌면 변경 파일만 재분석해 artifact를 갱신하고, "지난 run 이후 바뀐 것" diff 뷰를 제공한다.
- 사용자가 90초 이내에 PR의 의도, semantic impact와 상위 위험을 파악하게 한다.
- finding마다 코드·테스트·실행 결과와 미확인 사항을 제공한다.
- review 결과에서 승인된 invariant와 lesson을 축적하고, 지식이 stale해지면 감지한다.
- 한 repository·한 언어에서 시작해 다중 언어·다중 repository로 확장 가능한 artifact 계약을 만든다.

### 5.2 비목표

MVP에서는 다음을 목표로 하지 않는다.

- 자체 agent runtime·scheduler 구현 (호스트 agent를 사용한다)
- 전용 네이티브/Web 뷰어 앱 (HTML 템플릿과 후순위 `atlas serve`로 대체)
- repo 전체를 무제한 depth로 탐색하는 단일 HTML (slice 단위로 한정)
- 완전 자동 merge, 인간 승인 대체
- 모든 언어의 완전한 call/data-flow 분석
- agent가 자율적으로 production 코드를 수정·commit·push
- AI가 생성한 설명을 자동으로 repository truth로 승격

---

## 6. 대상 사용자

### 6.1 Primary: 새로운 저장소를 맡은 개발자

**목표:** architecture와 핵심 실행 경로를 빠르게 이해하고 첫 변경을 올바른 위치에 구현한다.
**사용 형태:** agent에게 "이 subsystem을 onboarding artifact로 만들어줘"라고 요청하고, 생성된 HTML을 브라우저에서 탐색한다.

### 6.2 Primary: AI 생성 PR을 검토하는 senior reviewer

**목표:** 코드 line 전체를 읽기보다 의도, 위험, 증거와 미확인 사항에 집중한다.
**사용 형태:** review SKILL이 생성한 Review Brief HTML과 Vim quickfix를 오간다.

### 6.3 Secondary: SKILL을 실행하는 호스트 agent

Atlas의 직접 소비자다. SKILL.md의 절차·금지행동을 따르고, 내장 scripts를 호출하며, artifact schema를 준수하는 JSON을 산출한다.

### 6.4 Secondary: Repository owner·tech lead

**목표:** 팀이 공유해야 할 invariant와 approval policy를 정의하고 knowledge의 정확성을 관리한다.

---

## 7. 주요 사용자 여정

### 7.1 코드베이스 onboarding

```text
agent에게 학습 목표 전달 ("결제 subsystem 이해하고 싶어")
→ agent가 onboarding SKILL 로드
→ 내장 scripts로 slice index 생성 (entry point + N-hop neighborhood)
→ agent가 조사 후 artifact JSON 산출
→ scripts가 뷰어 템플릿과 결합해 self-contained HTML 생성
→ 브라우저에서 architecture tour, call graph, 실행 경로 탐색
→ symbol 클릭으로 definition/reference 이동
→ predict-before-reveal, explain-back, localization task 수행
```

### 7.2 코드 최신화 후 incremental update

```text
main pull로 분석 대상 revision 변경
→ agent에게 갱신 요청 ("main 최신화됐어, artifact 갱신해줘")
→ scripts가 변경 파일과 영향받는 symbol chunk 탐지
→ agent가 변경 부분만 재조사
→ artifact 재생성 (미변경 chunk는 재사용)
→ HTML에 "since last run" diff 뷰 포함
→ 사용자는 바뀐 부분만 다시 학습
```

### 7.3 낯선 PR 리뷰

```text
agent에게 diff 전달
→ review SKILL 실행
→ semantic diff, deterministic check, verifier
→ Review Brief HTML + quickfix 생성
→ 90초 Brief 확인 → finding·spotlight 탐색
→ Vim quickfix로 코드 이동, evidence 확인
→ 승인·수정·반박·보류 결정
→ 승인된 finding을 invariant 후보로 저장
```

---

## 8. HTML Artifact 아키텍처

### 8.1 데이터/렌더러 분리

```text
Artifact (원본, 저장 대상)
  = JSON Manifest + Content-addressed Chunks

HTML Export (파생, 소비 대상)
  = Viewer Template (버전 관리)
  + Data Island (artifact JSON 인라인 임베드)
```

- Agent는 **JSON만** 생성한다. HTML 구조·스타일·인터랙션은 전부 템플릿의 책임이다.
- 템플릿은 SKILL 패키지에 버전과 함께 포함되고, artifact manifest는 렌더링에 사용된 `template_version`을 기록한다.
- 같은 artifact를 새 템플릿 버전으로 재렌더링할 수 있다. 렌더링은 결정적이다.

### 8.2 Self-contained가 기본인 이유

브라우저는 `file://`로 연 HTML에서 외부 파일 fetch를 CORS로 차단한다. 따라서 로컬 서버 없이 동작하려면 데이터·JS·CSS를 모두 단일 HTML에 인라인해야 한다. 이 제약을 기본 설계로 수용한다.

- 뷰어 JS/CSS: 템플릿에 인라인 (외부 CDN 의존 금지)
- artifact 데이터: `<script type="application/json">` data island로 임베드
- 코드 발췌·symbol graph: 압축 고려 (예: JSON을 base64+deflate로 임베드 후 뷰어에서 해제)

### 8.3 크기 예산

slice 단위 임베드를 전제로 다음 예산을 목표로 한다.

```yaml
target_html_size: "<= 5 MB"
hard_limit_html_size: "<= 15 MB"
```

예산 초과 시 scripts는 slice 축소(hop 수 감소, 발췌 길이 제한)를 제안하고, 어떤 항목이 잘렸는지 artifact에 명시한다. 침묵 truncation은 금지한다.

### 8.4 전용 뷰어가 필요해지는 임계점

다음이 필요해지는 시점에 같은 뷰어 템플릿을 로컬 서버로 서빙하는 `atlas serve`를 도입한다 (후순위 Phase). 별도 네이티브 뷰어는 만들지 않는다.

- slice 임베드 한계를 넘는 repo-scale 탐색
- `Open in editor` 같은 로컬 액션
- learner state 등 서버측 상태 저장·동기화

---

## 9. Slice 모델

### 9.1 정의

Slice는 하나의 artifact에 임베드되는 코드 범위다.

```yaml
slice:
  entry_points:            # 학습 목표가 지정하는 시작 symbol/file
  hop_limit: 2             # symbol graph 상 확장 깊이 (기본값, 조정 가능)
  includes:                # 명시적 추가 파일/디렉토리
  excludes:                # vendored, generated 등 제외
```

Slice에 포함되는 것:

- slice 내 파일의 소스 발췌 (전문 또는 관련 구간)
- slice 내 symbol의 definition/reference/call edge (사전 계산된 symbol graph)
- 관련 test·commit evidence pointer

### 9.2 Slice 내 navigation

뷰어는 임베드된 symbol graph를 사용해 다음을 제공한다. LSP 실시간 질의가 아니라 **snapshot 탐색**이다.

- symbol 클릭 → definition으로 이동
- find references (slice 내)
- call graph에서 node 클릭 → 코드 pane 이동
- symbol 검색

### 9.3 경계 처리

slice 밖을 가리키는 symbol은 **경계 표시(boundary marker)**로 렌더링한다.

- symbol 이름, 소속 파일, 한 줄 signature까지는 표시
- "이 slice에 포함되지 않음 — 확장하려면 재분석 필요" 안내
- 경계 이탈 시도는 로깅해 slice 기본 범위 조정의 근거로 사용

첫 버전에서 slice 확장은 "해당 entry point로 재분석 요청"으로 단순화한다. chunk 단위 부분 확장은 후속 단계다.

---

## 10. 뷰어 표준 컴포넌트

Agent가 생성하는 것은 아래 컴포넌트들이 소비하는 **데이터**뿐이다. 컴포넌트 자체는 템플릿에 고정된다.

| 컴포넌트 | 소비 데이터 | 용도 |
|---|---|---|
| Architecture map | module/dependency graph | subsystem 구조 개관 |
| Call graph | symbol call edges | 실행 경로의 정적 구조 |
| Execution flow | ordered trace steps + evidence pointer | 요청/event가 거치는 경로 |
| Symbol tree | file/symbol hierarchy | 탐색 진입점 |
| Code pane | 소스 발췌 + range highlight + cross-link | evidence 확인, go-to-def |
| Symbol search | symbol index | 이름 기반 탐색 |
| Diff view | 이전 run 대비 changed chunk | incremental update 확인 |
| Lesson panel | 질문/rubric/learner 응답 슬롯 | active learning loop |
| Review brief | intent, impact, findings, spotlight | 90초 리뷰 개관 |

모든 그래프 node와 flow step은 evidence pointer를 가지며 클릭 시 code pane으로 연결된다. figure와 산문은 역할을 나눈다 — figure는 구조를, 산문은 상세를 담당한다. figure가 설명을 대체하는 것이 아니라, 각 node·flow step에 로직 흐름의 상세한 annotation이 붙는다.

---

## 11. Incremental Update 파이프라인

### 11.1 원칙

비싼 것은 agent 분석이지 HTML 재생성이 아니다. Incrementality는 HTML 파일이 아니라 **데이터 파이프라인**의 속성이다. HTML은 매번 통째로 재생성한다(비용: 템플릿 렌더링뿐).

### 11.2 흐름

```text
새 revision 감지 (git diff base..head)
→ 변경 파일 목록
→ 영향받는 symbol chunk 식별 (content hash 비교 + reference neighborhood)
→ 미변경 chunk: 재사용
→ 변경 chunk: agent 재조사 대상으로 표시
→ agent가 변경 부분만 조사해 chunk 갱신
→ manifest 갱신, HTML 재생성
→ diff 뷰 데이터 생성 (chunk 단위 added/removed/modified)
```

### 11.3 Diff 뷰

old/new artifact가 모두 존재하므로 "지난 run 이후" 뷰를 1급으로 제공한다.

- 바뀐 symbol·파일·edge 목록
- 영향받은 설명·lesson·invariant (stale 후보)
- 사용자의 학습 이력과 교차해 "다시 봐야 할 것" 우선순위

---

## 12. SKILL 시스템

### 12.1 구성 요소

1. **SKILL.md** — 조사 절차, scripts 사용법, 권한, 검증 방법, 출력 규약과 금지 행동. 호스트 agent가 로드한다.
2. **Task Template** — 현재 작업의 목표, 범위, acceptance criteria와 위험 초점.
3. **Artifact Schema** — 결과의 기계 판독 가능한 JSON 계약. scripts가 검증한다.
4. **Viewer Template** — 버전 관리되는 HTML 렌더러.
5. **Scripts (`scripts/`)** — skill 패키지에 내장된 결정적 도구. symbol index, slice 계산, schema validation, HTML 렌더링, incremental diff. **agent만 호출하며 사람이 직접 실행하는 것을 전제하지 않는다.** SKILL·schema·template과 한 패키지로 배포되어 버전 skew가 발생하지 않는다.

### 12.2 실행 환경 요구사항

사용자가 설치해야 하는 binary는 두 개이며, 실질적인 신규 설치 대상은 `uv` 하나다.

| binary | 용도 | 비고 |
|---|---|---|
| `git` | diff·blame·history·revision 식별 | 개발 환경에 기본 존재 |
| `uv` | scripts 실행과 의존성 bootstrap | 유일한 실질 설치 대상 |

scripts는 PEP 723 inline dependencies로 작성하고 `uv run`으로 실행한다. Python 인터프리터와 wheel 의존성(tree-sitter core·언어별 grammar, jsonschema, zstandard)은 최초 실행 시 uv가 받아 캐시한다. 저장소는 Python stdlib의 SQLite를 사용하므로 별도 설치가 없다.

설치가 필요 없는 것:

- **LSP server** — MVP는 tree-sitter만 사용한다. LSP headless 도입(§16.2) 후에도 "있으면 정밀 index, 없으면 tree-sitter로 degrade"로 동작한다.
- **pytest·ruff·tsc 등 검증 도구** — Atlas의 의존성이 아니라 분석 대상 repo의 dev 환경이다. §13.3이 "프로젝트에 존재하는 도구만 탐지해 실행한다"인 이유다.
- **Node.js** — MVP 범위에서 불필요하다.

Network 정책은 단계로 구분한다.

```yaml
bootstrap:      network required      # 최초 1회, uv 의존성 다운로드
investigation:  network not required  # 조사·렌더링·검증은 오프라인 동작
```

prebuilt wheel이 없는 플랫폼에서는 grammar 컴파일에 C 컴파일러가 필요할 수 있다. bootstrap 실패는 침묵하지 않고 명확한 진단 메시지로 보고한다(§26 Scripts 의존성 배포).

### 12.3 Runtime 위임

Atlas는 자체 runtime·scheduler를 갖지 않는다. 실행은 호스트 agent(Claude Code 등)에 위임하고, 통제는 두 층으로 나눈다.

**SKILL.md가 지시하는 것 (권고, agent가 준수):**

```yaml
recommended_max_parallel_investigators: 3
worker_recursive_spawn: forbidden
investigation: read-only
network: denied during investigation   # bootstrap 예외는 §12.2
```

**scripts가 강제·검증하는 것 (결정적):**

- artifact schema validation (미준수 JSON은 렌더링 거부)
- evidence pointer의 path/range/content hash 실재 검증
- slice 경계와 크기 예산
- secret redaction

자연어 지시만으로 보장할 수 없는 항목은 반드시 scripts 검증으로 내린다.

### 12.4 기본 Review Workflow (SKILL이 기술하는 절차)

```text
Change Classifier
→ 최대 3개 read-only investigator
  ├── Semantic Impact
  ├── Test & Oracle
  └── History & Architecture
→ Deterministic Evidence Merger (scripts)
→ Independent Verifier (별도 session)
→ Single Report Writer
→ Risk-based Human Approval
```

### 12.5 기본 Learning Workflow

```text
Learning Goal Classifier
→ slice 계산 (scripts)
→ 최대 3개 read-only investigator
  ├── Architecture & Execution
  ├── Domain & Invariant
  └── History & Test
→ Deterministic Evidence Merger (scripts)
→ Lesson Generator
→ Independent Answer Evaluator
→ HTML 렌더링 (scripts)
```

작은 diff와 단일 subsystem 학습에는 single-agent fast path를 제공한다.

---

## 13. Review 기능 요구사항

### 13.1 Intent Lock

PR·issue·agent transcript에서 다음을 구조화해 작성자가 확인한다. AI가 추출한 요구사항은 확인 전까지 `proposed` 상태다.

```yaml
problem:
desired_behavior:
constraints:
non_goals:
acceptance_criteria:
risk_focus:
```

### 13.2 Semantic Diff

최소 분석 단위: changed symbol, caller·callee, interface·implementation, import·dependency, API/schema contract, read/write data store, state transition, error path, concurrency boundary, permission boundary, related test.

### 13.3 Deterministic Verification

프로젝트에 존재하는 도구만 탐지해 실행한다: build/compile, lint, type check, unit/integration test, SAST·secret scan, dependency scan, API/schema compatibility, migration·rollback validation. 기존 baseline failure와 변경으로 생긴 failure를 구분한다. 테스트 통과는 "현재 테스트가 관찰한 조건에서 통과"로 표현하고 correctness 증명으로 표시하지 않는다.

### 13.4 Risk Classification

| 위험 | 처리 |
|---|---|
| Low | 자동 검사, 선택적 human review |
| Medium | evidence report와 human reviewer |
| High | architecture/security/data owner 승인 |
| Critical | 다중 승인, rollback 검증, 자동 merge 금지 |

LLM은 evidence와 실패 가능성을 산출할 수 있지만 block/warn 정책은 deterministic policy(scripts)가 결정한다.

### 13.5 Finding Contract

```text
Claim → Risk scenario → Evidence → Missing evidence
→ Reproduction 또는 falsification → Suggested verification → Human decision
```

기본 게시 예산:

```yaml
max_blocking_findings: 3
max_warning_findings: 5
style_suggestions: disabled
```

이 예산은 finding 개수에만 적용된다. 변경된 코드의 로직 흐름 서술(semantic diff 설명, Review Brief의 impact 설명)은 예산 대상이 아니며, 리뷰어가 코드를 읽는 데 필요한 만큼 상세하게 제공한다.

### 13.6 Spotlight

결함으로 확정할 수 없지만 사람이 반드시 확인해야 하는 변경을 finding과 구분해 표시한다. 예: transaction boundary 변경, rollback 없는 migration, 높은 fan-out symbol, test 없는 failure branch.

### 13.7 Independent Verifier

Verifier는 별도 session에서 각 finding이 틀렸음을 증명하려 한다: 기존 guard 검색, 도달 가능성 확인, caller·callee 오해 확인, sandbox reproduction, 관련 test 실행, 의도된 동작 여부 확인. 구현 session의 전체 reasoning은 제공하지 않고 구조화된 requirement, claim, diff와 repository evidence만 제공한다.

---

## 14. Learning 기능 요구사항

### 14.1 Lesson 유형

architecture tour, request/data/event flow trace, domain concept comparison, invariant lesson, failure and recovery path, security/permission boundary, historical design decision, change localization task ("이 변경을 어디에 구현할 것인가?").

### 14.2 설명 상세도

Finding의 precision 예산은 학습 설명에 적용되지 않는다. 코드를 읽고 이해하는 데 필요한 부분은 수정·판단할 거리가 없어도 로직 흐름을 상세하게 설명한다.

Execution flow의 각 step은 다음을 포함한다.

- 무엇이 호출되고 어떤 값이 전달되는가
- 어떤 조건에서 분기하고, 각 분기가 어디로 이어지는가
- 어떤 상태·데이터가 읽히고 변경되는가
- 실패 시 어떤 error path로 빠지는가
- 해당 step의 evidence pointer

한 줄 요약("주문을 생성한다")으로 step을 대체하지 않는다. 상세함의 상한은 finding budget이 아니라 slice 크기 예산(§8.3)이며, 예산 초과 시 설명을 얕게 만드는 것이 아니라 slice 범위를 좁힌다.

### 14.3 Active Learning Loop

```text
학습 목표 선택 → predict before reveal → evidence 기반 설명
→ code/symbol 탐색 (artifact 내 navigation) → explain back
→ trace reconstruction → transfer task → feedback → spaced review
```

Self-contained HTML에서는 사용자 응답을 로컬(localStorage 또는 export 파일)에 저장한다. Evaluator 실행은 다음 agent run에서 수행한다.

### 14.4 Answer Evaluation

설명을 생성한 agent가 자기 문장을 정답으로 사용하지 않는다. 답변 평가는 code, test, commit과 사람이 승인한 rubric을 기준으로 별도 evaluator가 수행한다.

평가 상태: correct, partially correct, unsupported claim, misconception, unknown due to insufficient evidence. 모호할 경우 즉시 오답 처리하지 않고 근거를 묻는 후속 질문을 생성한다.

### 14.5 Learner Model

```yaml
concept_id:
level: 0-5      # 0 미노출 … 5 위험/trade-off를 타인에게 설명
attempts:
weak_points:
evidence_of_understanding:
last_reviewed_at:
next_review_at:
```

### 14.6 Review-Learning 연결

- 낯선 subsystem PR에는 선택적 3분 사전 학습을 제공한다.
- 승인된 finding은 invariant·lesson 후보로 전환한다.
- 관련 invariant가 영향을 받는 후속 PR에서 이전 lesson을 연결한다.
- incremental update의 diff 뷰는 learner model과 교차해 재학습 우선순위를 제시한다.

---

## 15. Artifact 및 저장 모델

### 15.1 논리 구조

```text
Artifact = Structured JSON Manifest + Content-addressed Chunks + Blob References
HTML Export = Viewer Template + Artifact (파생물)
```

### 15.2 세 저장 계층

1. **Repository Index** — commit별 symbol·definition·reference·graph. 재생성 가능.
2. **Run Artifact** — 특정 run의 immutable 결과. HTML export의 원본.
3. **Curated Knowledge** — 사람이 승인한 invariant·concept·rationale.

### 15.3 Artifact Manifest 최소 필드

```json
{
  "schema_version": "2.0",
  "artifact_id": "art_01K3...",
  "type": "learning",
  "repository": {
    "id": "org/repo",
    "base_commit": "abc123",
    "head_commit": "def456"
  },
  "slice": {
    "entry_points": ["python:apps.api.order_service:OrderService"],
    "hop_limit": 2,
    "file_count": 42,
    "truncations": []
  },
  "inputs": {
    "skill_version": "1.3.0",
    "template_version": "0.9.2",
    "schema_hash": "sha256:...",
    "model": "provider/model",
    "tool_versions": {}
  },
  "previous_artifact_id": "art_01K2...",
  "chunks": [],
  "findings": [],
  "lessons": [],
  "unknowns": [],
  "attachments": []
}
```

`previous_artifact_id`는 incremental update chain과 diff 뷰의 기반이다.

### 15.4 Evidence Pointer (v0.1과 동일)

```json
{
  "type": "symbol",
  "repository": "org/repo",
  "commit": "def456",
  "symbol_id": "python:apps.api.order_service:OrderService.create",
  "path": "apps/api/order_service.py",
  "range": {
    "start_line": 184,
    "start_character": 8,
    "end_line": 191,
    "end_character": 29
  },
  "content_hash": "sha256:...",
  "excerpt": "idempotency_key = uuid4()"
}
```

`symbol_id`는 semantic navigation, `path + range`는 fallback, `content_hash`는 stale 감지와 incremental update의 기반이다.

### 15.5 Chunk

Incremental update의 단위. file 또는 symbol group 단위로 content-addressed 저장한다.

```json
{
  "chunk_id": "sha256:...",
  "scope": "file:apps/api/order_service.py",
  "source_hash": "sha256:...",
  "produced_by_run": "art_01K2...",
  "payload": { "symbols": [], "edges": [], "annotations": [] }
}
```

source hash가 동일하면 재사용하고, 다르면 agent 재조사 대상으로 표시한다.

### 15.6 로컬 저장

```text
~/.local/share/codeatlas/
├── codeatlas.db            # SQLite: 검색·관계·상태
├── artifacts/
│   └── <artifact-id>.json.zst
├── exports/
│   └── <artifact-id>.html  # 재생성 가능한 파생물
├── chunks/
│   └── sha256/<prefix>/<hash>
├── blobs/
│   └── sha256/<prefix>/<hash>
└── repositories/
    └── <repo-id>/indexes/
```

Repository에는 생성 데이터를 넣지 않고 선택적으로 설정만 commit한다.

```text
.codeatlas/
├── config.yaml
├── skills/
├── templates/
├── schemas/
├── knowledge/
└── policies/
```

### 15.7 Working Tree Revision

commit되지 않은 변경은 synthetic revision으로 식별한다.

```yaml
type: worktree
base_commit: abc123
patch_hash: sha256:...
workspace_hash: sha256:...
```

---

## 16. Code Intelligence

### 16.1 원칙

"IDE의 도움 없이"는 symbol 해석을 포기한다는 뜻이 아니다. Symbol 해석은 **skill 내장 scripts가 담당**하고, agent와 뷰어는 그 결과를 소비한다. Agent가 grep만으로 call graph를 추측하게 하지 않는다.

### 16.2 분석 계층

```text
Tree-sitter (기본)
→ 언어 공통 AST, symbol 추출, 경량 def/ref

LSP headless (선택)
→ 정밀 definition·reference·type. run 단위로 scripts가 기동·종료

SCIP/LSIF (후순위)
→ commit별 persistent index, 다중 repo

Git
→ history·blame·base/head diff

Test/Runtime
→ test mapping·coverage (후속 단계)
```

MVP는 tree-sitter 기반 indexer로 시작하고, 정밀도가 필요한 언어부터 LSP headless를 추가한다. Index 불가 구간은 path/range로 degrade하고 `unknown`으로 표시한다.

### 16.3 정규화

LSP wire response를 그대로 보존하지 않고 normalized internal schema(symbol, edge, range, hash)로 변환해 chunk에 저장한다. Completion, semantic token, hover payload 전체는 저장하지 않는다.

### 16.4 증분 Index

```text
최초 연결 → slice 대상 symbol index
새 commit → 변경 파일 탐지 → 관련 symbol과 reference neighborhood만 갱신
artifact 생성 → 기존 index 조회 → 부족한 정보만 추가 질의
```

---

## 17. 인터페이스와 출력물

### 17.1 사람의 인터페이스는 둘이다

1. **Agent와의 대화** — 분석 요청, 갱신 요청, slice 조정, finding 재검토. 사람이 별도 명령을 배우지 않는다.
2. **HTML artifact** — 탐색·학습·리뷰의 주 소비 형태.

scripts는 agent 전용 도구이며 사람이 직접 실행하는 것을 전제하지 않는다. 사람용 CLI를 별도로 배포하지 않는다.

### 17.2 Run 출력물

한 run은 다음을 출력한다.

```text
<artifact-id>.html    # self-contained 뷰어 (주 출력)
<artifact-id>.json    # artifact 원본 (schema 검증 대상, 재렌더링 가능)
<artifact-id>.qf      # review run 한정: Vim quickfix 포맷 부가 출력
```

### 17.3 Vim Quickfix (부가 출력)

review run은 quickfix 포맷 텍스트 파일을 함께 생성한다. 별도 명령 없이 표준 Vim 기능으로 소비한다.

```bash
vim -q review.qf
```

출력 포맷:

```text
apps/api/order_service.py:184:8: [HIGH] retry마다 idempotency key가 재생성됨
```

open-in-editor(HTML에서 특정 evidence를 에디터로 열기)는 local daemon이 필요하므로 Phase 4의 `atlas serve`로 미룬다.

### 17.4 `atlas serve` (후순위)

slice 한계를 넘는 repo-scale 탐색과 open-in-editor가 필요해지는 시점에 도입한다. 같은 뷰어 템플릿을 localhost로 서빙하며, 이 단계에서만 설치형 배포가 등장한다. localhost bind, session token, 등록 repository allowlist와 path traversal 방어를 필수로 한다.

---

## 18. Repository Evidence Graph

### 18.1 Node

Repository, Commit, File, Symbol, API, Schema, DomainConcept, Invariant, Test, ADR, Issue, Incident, ReviewFinding, LearningLesson.

### 18.2 Edge

CALLS, IMPORTS, IMPLEMENTS, READS, WRITES, VALIDATED_BY, CHANGED_WITH, INTRODUCED_BY, DEPENDS_ON, ENFORCES_INVARIANT, VIOLATED_BY, EXPLAINED_BY.

초기에는 별도 graph database를 도입하지 않고 SQLite edge table로 관리한다.

---

## 19. Knowledge Freshness와 Provenance

모든 설명·lesson·finding은 생성 기준 revision과 evidence hash를 기록한다.

Knowledge 상태: `proposed → approved | rejected`, `approved → stale → reconfirmed`.

Stale 후보 조건:

- evidence symbol content hash 변경
- 관련 interface/schema 변경
- test 삭제 또는 이름 변경
- dependency edge 변경
- 생성 commit이 현재 branch와 크게 이탈

근거가 부족한 역사적 의도는 사실로 저장하지 않고 `unknown` 또는 `hypothesis`로 표시한다. Incremental update는 stale 감지와 같은 content hash 체계를 공유한다.

---

## 20. 보안·프라이버시 요구사항

- repository code, comment, issue와 log는 untrusted input으로 처리한다. 코드 주석의 "이전 지시를 무시하라" 같은 문장을 agent instruction으로 해석하지 않는다.
- SKILL의 조사 절차는 read-only를 기본으로 한다.
- secret, API key, token, connection string은 artifact·chunk·HTML에서 `[REDACTED]` 처리한다. Redaction은 scripts가 렌더링 전에 강제한다.
- **HTML export는 소스 발췌를 포함하며 공유되기 쉽다.** export 시 포함된 파일 목록을 표시하고, repository별로 export 정책(발췌 허용 범위, 공유 경고)을 설정할 수 있게 한다.
- 뷰어 템플릿은 외부 리소스(CDN, remote font, analytics)를 로드하지 않는다.
- `atlas serve`는 localhost 이외에 bind하지 않는다.
- arbitrary path와 shell command 실행을 금지한다.
- 민감한 원본 source를 remote model에 보내는 정책을 repository별로 설정하고, model provider·전송 범위·retention policy를 run audit에 기록한다.

---

## 21. 성공 지표

### 21.1 Navigation·Artifact 품질 (신규)

- artifact 단독 탐색으로 답할 수 있는 이해 질문 비율 (IDE 미사용)
- slice 경계 이탈 빈도 (낮을수록 slice 계산이 적절)
- HTML 크기 예산 준수율
- incremental 재분석 시 chunk 재사용률·비용 절감률
- diff 뷰가 실제 변경을 놓친 비율

### 21.2 Review 품질

blocker precision, warning precision, false positive per PR, historical human finding recall, seeded defect detection, escaped defect, time-to-first-correct-risk, human active review time, confidence calibration.

초기 목표:

```yaml
blocker_precision: ">= 95%"
warning_precision: ">= 75%"
median_false_positive_per_pr: "<= 1"
```

### 21.3 Learning 품질

execution path reconstruction accuracy, domain concept explanation score, invariant recall, task localization accuracy, transfer task 성공률, 7일·30일 retention, 첫 유효 PR까지 걸린 시간.

### 21.4 최적화하지 않을 지표

생성 댓글 수, 읽은 line·파일 수, lesson 클릭 수, artifact 체류시간, acceptance rate 단독 지표.

---

## 22. Evaluation Plan

- **Historical Replay** — 리뷰 전 상태의 과거 PR만 제공하고 당시 human finding·후속 fix와 비교한다.
- **Seeded Defect·Mutation** — 조건 반전, auth check 제거, retry 중복, transaction boundary 변경 등을 주입해 precision/recall을 측정한다.
- **Navigation Task Evaluation (신규)** — 동일 subsystem에 대해 "IDE 사용 그룹 vs artifact 단독 그룹"으로 path reconstruction, change localization 정확도·소요 시간을 비교한다.
- **Incremental Correctness (신규)** — full 재분석 결과와 incremental 결과를 diff해 chunk 재사용이 오류를 만들지 않는지 검증한다.
- **Learning Evaluation** — 사전·사후·지연(7일) 평가: path reconstruction, invariant explanation, change localization, 실제 PR defect detection.

---

## 23. MVP 범위

### 23.1 MVP 대상

- 로컬 repository 한 개, 언어 한 개 (TypeScript 또는 Python)
- 호스트 agent: Claude Code (SKILL 계약은 agent-agnostic하게 설계)
- Git repository, commit 또는 working-tree diff
- local SQLite/chunk/blob storage
- tree-sitter 기반 indexer

### 23.2 MVP 기능

#### Learning (우선)

- onboarding SKILL: entry point 탐색, slice 계산, 핵심 execution path 1개, domain concept와 invariant
- 모든 설명에 symbol evidence
- self-contained HTML export: architecture map, call graph, execution flow, symbol tree, code pane, symbol search
- slice 내 go-to-definition·find-references
- predict-before-reveal 1개, explain-back 1개, localization task 1개
- incremental update: 변경 chunk 탐지, 부분 재분석, diff 뷰

#### Review

- review SKILL: task template 입력, base/head semantic diff
- 기존 lint/type/test 실행 결과 수집
- 최대 3개 finding, evidence pointer, independent verifier
- Review Brief HTML + quickfix 부가 출력

#### 공통

- artifact JSON schema + scripts validation
- content-addressed chunk 저장, stale content hash 확인
- secret redaction (scripts 강제)

### 23.3 MVP 제외

- `atlas serve`, 다중 언어, SCIP, 자동 merge, production runtime trace, graph database, team SSO·RBAC, agent의 코드 수정

---

## 24. 단계별 출시 계획

### Phase 0 — Artifact Contract & Viewer Template

- JSON Schema, evidence pointer, chunk 포맷, run metadata 정의
- 뷰어 템플릿 v0: architecture map, call graph, code pane, symbol tree, symbol search
- sample artifact(fixture)로 HTML 렌더링·검증

**Exit:** agent 없이 fixture artifact를 HTML로 렌더링하고, 브라우저에서 slice 내 go-to-definition이 동작한다.

### Phase 1 — Onboarding SKILL + Scripts

- index script (tree-sitter), slice 계산
- onboarding SKILL.md 작성, artifact schema validation
- render script, redaction
- Claude Code에서 end-to-end 실행

**Exit:** 실제 repository의 subsystem 하나를 SKILL로 분석해 HTML artifact를 생성하고, IDE 없이 실행 경로를 재구성할 수 있다.

### Phase 2 — Incremental Update

- chunk 저장·재사용, 변경 탐지, update script
- diff 뷰, stale knowledge 표시

**Exit:** main 최신화 후 재실행 시 변경 chunk만 재분석되고, 사용자가 diff 뷰에서 바뀐 부분만 확인할 수 있다.

### Phase 3 — Review SKILL + 통합

- review SKILL, deterministic checks, verifier, Review Brief HTML
- quickfix 부가 출력
- 승인된 finding → invariant proposal → 후속 lesson 연결

**Exit:** 한 review에서 승인된 invariant가 후속 lesson과 PR 분석에 재사용된다.

### Phase 4 — Scale & Serve

- `atlas serve`: repo-scale 탐색, open-in-editor
- LSP headless 정밀 index, SCIP 검토
- 다중 repository, 팀 공유(미결정 사항 재검토)

**Exit:** slice 한계를 넘는 탐색이 같은 뷰어로 가능하다.

---

## 25. MVP Acceptance Criteria

1. 사용자가 IDE 없이, agent에 SKILL 실행을 요청하는 것만으로 repository subsystem의 HTML artifact를 얻을 수 있다.
2. HTML artifact는 단일 파일이고 외부 네트워크 리소스를 로드하지 않으며, `file://`로 열어 모든 기능이 동작한다.
3. artifact 안에서 slice 내 symbol의 definition/reference로 클릭 이동할 수 있고, slice 밖 symbol은 경계 표시로 렌더링된다.
4. 핵심 설명이 산문 단독이 아니라 최소 하나의 interactive figure(call graph 또는 execution flow)와 연결되며, figure의 node가 code pane으로 연결된다.
5. 분석 대상 revision 변경 후 재실행 시 미변경 chunk가 재사용되고(재사용률 측정 가능), diff 뷰가 chunk 단위 변경을 표시한다.
6. artifact JSON이 schema validation을 통과하고, agent가 생성한 HTML이 아니라 scripts가 템플릿으로 렌더링한 HTML만 산출된다.
7. 모든 게시 finding·설명이 최소 하나의 revision-fixed evidence pointer를 가지며, scripts가 pointer의 path/range/hash 실재를 검증한다.
8. review run이 quickfix 포맷 파일을 함께 생성하고, `vim -q`로 열어 정확한 file/line으로 이동할 수 있다.
9. reviewer agent와 verifier가 별도 session에서 실행된다.
10. test pass가 correctness proof로 표현되지 않는다.
11. onboarding lesson이 최소 하나의 예측 질문, 자기 설명과 localization task를 포함한다.
12. secret-like 값이 artifact·chunk·HTML에서 redaction되고, 이는 scripts가 렌더링 전에 강제한다.
13. finding이 없는 경우 억지로 suggestion을 생성하지 않고 `no verified findings`를 반환한다.
14. HTML 크기 예산 초과 시 침묵 truncation 없이 잘린 항목이 artifact에 명시된다.

---

## 26. 주요 위험과 완화

### Agent의 schema 이탈

Agent가 JSON 계약을 정확히 지키지 못할 수 있다.
→ scripts의 schema validation을 렌더링 게이트로 강제, 실패 시 구체적 오류를 agent에 반환해 재시도, schema에 예시 포함.

### Slice 경계가 실제 이해 요구와 불일치

사용자가 자주 경계 밖으로 나가고 싶어질 수 있다.
→ 경계 이탈 로깅, hop 수·entry point 조정 가이드, "재분석 요청" 흐름 단순화, Phase 4에서 `atlas serve`로 해소.

### HTML 크기 폭증

발췌·graph 임베드가 예산을 초과할 수 있다.
→ 크기 예산과 명시적 truncation, 압축 임베드, slice 축소 제안.

### Incremental 결과의 불일치

chunk 재사용이 full 재분석과 다른 결과를 만들 수 있다.
→ neighborhood 변경 시 보수적으로 chunk 무효화, full 재분석과의 diff 검증을 evaluation에 포함.

### False Positive Fatigue

→ finding budget, verifier, reproducible evidence 우선, style suggestion 비활성화.

### Knowledge Hallucination

→ 모든 claim에 evidence, approved/proposed 분리, commit/hash freshness, unknown 허용.

### Context Explosion

→ diff → symbol neighborhood 순 단계적 retrieval, 전체 repository prompt 금지, slice 기반 context 한정.

### Prompt Injection

→ code/data와 agent instruction 채널 분리, read-only 조사, schema validation, scripts allowlist.

### 호스트 agent 간 동작 차이

SKILL 계약이 Claude Code 외 agent에서 다르게 동작할 수 있다.
→ 통제의 핵심을 scripts 검증으로 내리고, SKILL.md는 절차 기술에 집중. MVP는 Claude Code로 한정하되 agent-specific 기능 의존을 피한다.

### Scripts 의존성 배포

tree-sitter native binding 등 무거운 의존성을 skill 패키지의 scripts로 배포하면 실행 환경마다 설치 실패가 발생할 수 있다.
→ uv inline dependencies(PEP 723) 같은 self-bootstrapping 실행 방식 우선 검토, 최초 실행 시 의존성 검증 step을 SKILL.md에 명시, 실패 시 명확한 진단 메시지.

---

## 27. 미결정 사항

1. MVP 첫 언어: Python 또는 TypeScript
2. 뷰어 템플릿 기술 스택: vanilla JS vs 경량 프레임워크 인라인(예: preact 번들 임베드)
3. slice 기본 hop 수와 발췌 정책 (전문 vs 관련 구간)
4. prebuilt wheel이 없는 플랫폼의 fallback 전략 (컴파일 요구 vs vendored wheel vs 해당 플랫폼 미지원 선언) — scripts 실행 방식 자체는 Python + uv inline deps로 결정(§12.2)
5. LSP headless 도입 시점과 대상 언어
6. repository 설정을 `.codeatlas/`로 commit할지 전역 사용자 설정만 사용할지
7. remote model에 전송 가능한 code 범위와 기본 privacy policy
8. learner progress를 HTML localStorage / 로컬 저장소 중 어디에 두고 어떻게 동기화할지
9. artifact schema 공개·plugin API 제공 여부
10. 팀 공유(GitHub App, SSO·RBAC) 도입 여부와 시기

---

## 28. 권장 첫 번째 vertical slice

```text
로컬 Python repository
→ Claude Code에서 onboarding SKILL 실행
→ index script로 slice 계산 (entry point + 2-hop)
→ agent가 execution path 1개·invariant 조사, artifact JSON 산출
→ schema validation 통과
→ render script로 self-contained HTML 생성
→ 브라우저(file://)에서 call graph → code pane 이동, go-to-definition 탐색
→ main 최신화 후 "갱신해줘" → update script가 변경 chunk 탐지 → 변경분만 재조사 → diff 뷰 확인
```

이 vertical slice가 검증하는 핵심 가설:

- agent가 schema를 준수하는 데이터를 안정적으로 생성할 수 있는가
- slice 임베드만으로 유의미한 depth의 navigation이 되는가
- figure 중심 UX가 산문 요약보다 이해 속도를 높이는가
- chunk 재사용이 incremental 비용을 실제로 줄이는가
- 데이터/템플릿 분리가 뷰어 개선을 축적 가능하게 하는가

---

## 29. 최종 제품 정의

> Atlas는 agent에게 SKILL과 내장 scripts를 제공해 코드베이스를 조사하게 하고, 그 결과를 schema 검증된 JSON artifact로 저장하며, 버전 관리되는 뷰어 템플릿으로 self-contained HTML을 생성하는 repository intelligence 도구다. 사람은 agent와의 대화로 분석을 요청하고, 브라우저에서 코드 근거를 탐색하고 판단한다.

제품의 핵심 자산은 LLM의 설명 문장 자체가 아니라 다음의 결합이다.

```text
Versioned Repository Evidence (chunk + symbol graph)
+ Schema-validated Artifacts
+ Versioned Viewer Template
+ Human-approved Invariants
+ Independent Verification
+ Learner Understanding State
```

이를 통해 onboarding은 요약 소비가 아니라 실제 변경을 수행할 수 있는 이해를 형성하고, review는 더 적은 line을 읽고도 더 중요한 위험을 찾으며, 코드가 바뀌어도 지식은 바뀐 만큼만 갱신된다.
