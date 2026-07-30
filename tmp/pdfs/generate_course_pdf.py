from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "output" / "pdf" / "마토_오프라인_자료.pdf"
FONT_PATH = Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf")

NAVY = colors.HexColor("#16324F")
BLUE = colors.HexColor("#1F6AA5")
SKY = colors.HexColor("#EAF4FB")
MINT = colors.HexColor("#E9F6F2")
GREEN = colors.HexColor("#22735B")
YELLOW = colors.HexColor("#FFF6D9")
ORANGE = colors.HexColor("#B86416")
INK = colors.HexColor("#1F2933")
MUTED = colors.HexColor("#5B6770")
LINE = colors.HexColor("#D8E1E8")
WHITE = colors.white


def setup_fonts() -> None:
    pdfmetrics.registerFont(TTFont("AppleGothic", str(FONT_PATH)))


def styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "cover_kicker": ParagraphStyle(
            "cover_kicker", parent=base["Normal"], fontName="AppleGothic", fontSize=12,
            leading=17, textColor=SKY, alignment=TA_CENTER,
        ),
        "cover_title": ParagraphStyle(
            "cover_title", parent=base["Title"], fontName="AppleGothic", fontSize=27,
            leading=38, textColor=WHITE, alignment=TA_CENTER, spaceAfter=10,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub", parent=base["Normal"], fontName="AppleGothic", fontSize=13,
            leading=21, textColor=colors.HexColor("#DCEAF6"), alignment=TA_CENTER,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base["Heading1"], fontName="AppleGothic", fontSize=20,
            leading=29, textColor=NAVY, spaceBefore=2, spaceAfter=9,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontName="AppleGothic", fontSize=14,
            leading=21, textColor=BLUE, spaceBefore=9, spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "body", parent=base["BodyText"], fontName="AppleGothic", fontSize=10.3,
            leading=17, textColor=INK, spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "small", parent=base["BodyText"], fontName="AppleGothic", fontSize=8.7,
            leading=13, textColor=MUTED,
        ),
        "label": ParagraphStyle(
            "label", parent=base["Normal"], fontName="AppleGothic", fontSize=9,
            leading=13, textColor=BLUE,
        ),
        "box": ParagraphStyle(
            "box", parent=base["BodyText"], fontName="AppleGothic", fontSize=10,
            leading=15, textColor=INK,
        ),
        "box_title": ParagraphStyle(
            "box_title", parent=base["Heading3"], fontName="AppleGothic", fontSize=11.5,
            leading=16, textColor=NAVY, spaceAfter=2,
        ),
        "code": ParagraphStyle(
            "code", parent=base["Code"], fontName="AppleGothic", fontSize=9.2,
            leading=15, textColor=INK,
        ),
        "table": ParagraphStyle(
            "table", parent=base["BodyText"], fontName="AppleGothic", fontSize=8.8,
            leading=13, textColor=INK,
        ),
        "table_head": ParagraphStyle(
            "table_head", parent=base["BodyText"], fontName="AppleGothic", fontSize=8.8,
            leading=13, textColor=WHITE,
        ),
    }


S = None


def p(text: str, style: str = "body") -> Paragraph:
    return Paragraph(text, S[style])


def page_number(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
    canvas.setFont("AppleGothic", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 8.8 * mm, "Mato Blog Codex | 처음 배우는 블로그 자동화")
    canvas.drawRightString(A4[0] - 18 * mm, 8.8 * mm, f"{doc.page}")
    canvas.restoreState()


def card(title: str, text: str, color=SKY) -> Table:
    body = [p(title, "box_title"), p(text, "box")]
    table = Table([[body]], colWidths=[174 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), color),
        ("BOX", (0, 0), (-1, -1), 0.7, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table


def flow(items: list[tuple[str, str]], widths: list[float] | None = None) -> Table:
    widths = widths or [34 * mm] * len(items)
    cells = []
    for title, desc in items:
        cells.append([p(title, "box_title"), p(desc, "small")])
    table = Table([cells], colWidths=widths)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SKY),
        ("BOX", (0, 0), (-1, -1), 0.7, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.7, WHITE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return table


def bullet(text: str) -> Paragraph:
    return p(f"<font color='#1F6AA5'>●</font> {text}")


def section(title: str, kicker: str | None = None) -> list:
    parts = []
    if kicker:
        parts.append(p(kicker, "label"))
    parts.append(p(title, "h1"))
    return parts


def table(rows: list[list[str]], col_widths: list[float], header=True) -> Table:
    converted = []
    for r_index, row in enumerate(rows):
        converted.append([p(cell, "table_head" if header and r_index == 0 else "table") for cell in row])
    result = Table(converted, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.55, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
    ]
    for index in range(1, len(rows)):
        if index % 2 == 0:
            style.append(("BACKGROUND", (0, index), (-1, index), colors.HexColor("#F7FAFC")))
    result.setStyle(TableStyle(style))
    return result


def build() -> None:
    global S
    setup_fonts()
    S = styles()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    frame = Frame(18 * mm, 18 * mm, A4[0] - 36 * mm, A4[1] - 36 * mm, id="normal")
    doc = BaseDocTemplate(
        str(OUTPUT), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=20 * mm,
        pageTemplates=[PageTemplate(id="main", frames=[frame], onPage=page_number)],
        title="Codex로 네이버 블로그 작업하기",
        author="Mato Blog Codex",
    )

    story = []

    # Cover
    cover_inner = Table([
        [p("코딩을 몰라도 따라갈 수 있는 입문 강의", "cover_kicker")],
        [p("Codex로 네이버 블로그<br/>작업하기", "cover_title")],
        [p("마트 장보기와 서울 데이트 예시로 풀어보는<br/>문서, 스킬, 반복 작업, 검수, 토큰 이야기", "cover_sub")],
    ], colWidths=[150 * mm], rowHeights=[24 * mm, 65 * mm, 38 * mm])
    cover_inner.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    cover = Table([[cover_inner]], colWidths=[174 * mm], rowHeights=[235 * mm])
    cover.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 13),
        ("RIGHTPADDING", (0, 0), (-1, -1), 13),
    ]))
    story += [Spacer(1, 5 * mm), cover, PageBreak()]

    # 1
    story += section("1. 블로그 자동화 작업의 전체 흐름", "마트에서 장을 보는 순서와 비교하면 쉽게 이해할 수 있습니다")
    story += [p("가족이 “이마트에 가서 카레 재료를 사 와”라고 부탁한 상황을 생각해 보겠습니다. 필요한 재료를 확인하지 않은 채 아무 물건이나 담지는 않습니다. 몇 명이 먹는지 확인하고, 살 물건을 적은 뒤, 매장을 돌며 하나씩 담아 계산대로 갑니다.")]
    story += [card("블로그 작업도 같은 순서로 진행됩니다", "사용자가 주제와 원고 수를 말하면 Codex는 먼저 요청을 정리합니다. 그다음 필요한 자료를 살펴보고 서로 다른 원고를 씁니다. 완성된 글은 바로 올리지 않고, 빠진 내용이나 겹치는 표현이 없는지 검수합니다. 마지막으로 사용자가 지정한 블로그에 임시저장하거나 발행합니다.", MINT)]
    story += [Spacer(1, 5 * mm), flow([
        ("1. 주문 듣기", "주제와 원고 수를 알아듣기"),
        ("2. 재료 찾기", "쓸 만한 정보만 골라 보기"),
        ("3. 글 쓰기", "요청한 수만큼 새로 쓰기"),
        ("4. 빠진 것 보기", "형식과 내용이 맞는지 살피기"),
        ("5. 저장하기", "정해진 블로그에 차례로 넣기"),
    ], [34.8 * mm] * 5)]
    story += [Spacer(1, 6 * mm), p("네이버 비밀번호를 Codex에게 알려 줄 필요는 없습니다. 사용자가 전용 크롬 창에서 직접 로그인하면, 로그인에 필요한 브라우저 정보가 해당 프로필 폴더에 저장됩니다.", "small"), PageBreak()]

    # 2
    story += section("2. README, AGENTS.md, SKILL.md의 차이", "각 문서는 사용하는 시점과 목적이 다릅니다")
    story += [p("이마트 입구에는 영업시간과 층별 안내가 있고, 매장 안에는 이용 수칙이 있으며, 내 손에는 오늘 살 물건을 적은 목록이 있습니다. 셋 다 글이 적힌 종이지만 쓰는 때가 다르죠. 프로젝트 안의 문서도 마찬가지입니다.")]
    story += [table([
        ["문서 이름", "마트에서는", "이 프로젝트에서는"],
        ["README.md", "입구에서 보는 매장 안내", "설치 방법과 프로필 준비처럼 처음 알아야 할 내용을 모아 둡니다."],
        ["AGENTS.md", "매장 전체가 지키는 이용 수칙", "이 파일이 있는 프로젝트라면 Codex가 반드시 따라야 할 약속이 적혀 있습니다."],
        ["SKILL.md", "장을 보는 순서가 적힌 메모", "자료를 찾고, 글을 쓰고, 검사하고, 올리는 순서를 Codex에게 알려 줍니다."],
        ["그 밖의 MD", "필요할 때 찾아보는 품목 안내", "사진을 어떻게 다룰지, 프로필에 문제가 생기면 무엇을 볼지 자세히 적어 둡니다."],
    ], [34 * mm, 43 * mm, 97 * mm])]
    story += [Spacer(1, 5 * mm), card("처음 설치할 때는 README부터 확인합니다", "새로 설치하거나 프로필을 만들 때는 README를 읽습니다. 실제 블로그 작업을 시작하면 Codex가 SKILL.md와 안전 규칙을 읽습니다. 사진이나 로그인처럼 특정 문제가 생겼을 때만 관련 MD를 따로 확인합니다. 모든 문서를 매번 처음부터 끝까지 읽지는 않습니다.", YELLOW)]
    story += [Spacer(1, 5 * mm), p("MD는 Markdown의 줄임말입니다. 특별한 프로그램 없이 메모장으로도 열 수 있는 안내 문서입니다. 이 저장소에는 현재 AGENTS.md가 없으며, 공통 안전 규칙과 SKILL.md가 그 역할을 나누어 맡고 있습니다.") , PageBreak()]

    # Big framework
    story += section("문서와 기능은 필요한 시점에 사용합니다", "마트에서도 안내판과 계산대 점검표를 매번 함께 보지는 않습니다")
    story += [p("README, 스킬, 루프, 테스트 하네스는 한 줄로 이어지는 작업 순서가 아닙니다. 어떤 것은 처음 설치할 때만 보고, 어떤 것은 원고를 쓸 때마다 쓰며, 어떤 것은 프로그램 코드를 고친 날에만 꺼냅니다.")]
    story += [flow([
        ("처음 준비할 때", "README를 보고 프로필을 만듭니다"),
        ("글을 쓰기 직전", "안전 규칙과 스킬을 읽습니다"),
        ("여러 글을 쓸 때", "같은 과정을 원고마다 되풀이합니다"),
        ("코드를 고친 날", "테스트 하네스로 고장을 찾습니다"),
    ], [43.5 * mm] * 4)]
    story += [Spacer(1, 5 * mm), table([
        ["이름", "꺼내 보는 때", "마트에 비유하면", "실제 블로그 작업"],
        ["README", "설치하거나 프로필을 만들 때", "입구의 매장 안내판", "설치법, 프로필 등록법, 첫 요청 예시를 찾습니다."],
        ["AGENTS.md", "이 파일이 있는 프로젝트에서", "매장 이용 수칙", "이 프로젝트 안에서 꼭 지킬 약속을 Codex가 읽습니다."],
        ["SKILL.md", "블로그 작업을 시작할 때", "오늘의 장보기 목록", "조사부터 저장까지 어떤 순서로 움직일지 정합니다."],
        ["세부 MD", "사진이나 로그인처럼 막히는 곳이 생길 때", "상품 옆 상세 안내", "그 문제와 관련된 설명만 확인합니다."],
        ["루프", "원고가 두 개 이상일 때", "목록의 물건을 하나씩 담는 과정", "원고마다 쓰기와 검사를 같은 순서로 되풀이합니다."],
        ["결과 검수", "저장 버튼을 누르기 직전", "계산 전 장바구니 확인", "원고 수, 제목, 소제목, 사진이 모두 맞는지 봅니다."],
        ["테스트 하네스", "프로그램 코드를 손본 뒤", "계산대 기계 점검", "고친 기능 때문에 다른 기능이 망가지지 않았는지 시험합니다."],
    ], [27 * mm, 40 * mm, 43 * mm, 64 * mm])]
    story += [Spacer(1, 4 * mm), p("결과 검수와 테스트 하네스는 확인 대상이 다릅니다. 결과 검수는 방금 작성한 원고를 확인하는 일이고, 테스트 하네스는 프로그램 기능이 제대로 작동하는지 시험하는 도구입니다.", "small"), PageBreak()]

    # Big framework example
    story += section("URL을 받은 뒤 원고 세 편을 임시저장하는 순서", "서울 데이트 원고를 예로 전체 작업 과정을 연결합니다")
    story += [card("사용자의 요청", "이 URL을 참고해서 ‘서울 데이트 코스’ 원고 3개를 만들어줘. 글마다 제목과 구성을 다르게 하고, 프로필 1, 2, 3에 임시저장해줘.", YELLOW)]
    story += [Spacer(1, 4 * mm), card("README부터 다시 읽을 필요는 없습니다", "이미 설치와 프로필 준비를 끝냈다면 곧바로 글쓰기 작업으로 들어갑니다. 테스트 하네스도 실행하지 않습니다. 테스트 하네스는 로그인 유지 기능처럼 프로그램 코드를 고쳤을 때 쓰는 점검 도구이기 때문입니다.", SKY)]
    story += [Spacer(1, 4 * mm), table([
        ["이때 보는 기준", "차례", "Codex가 하는 일", "서울 데이트 원고에서는"],
        ["안전 규칙 + SKILL.md", "1", "주문을 정확히 듣기", "URL, 주제, 원고 3개, 프로필 1·2·3, 임시저장을 한 묶음으로 읽습니다."],
        ["자료 사용 규칙 MD", "2", "쓸 재료 고르기", "사람들이 많이 궁금해하는 장소와 이동 정보를 찾되, 남이 쓴 문장은 가져오지 않습니다."],
        ["공통.txt + 원고 규칙", "3", "글투와 형식 잡기", "공통 말투와 Mato 형식을 불러옵니다. 실제로 다녀온 것처럼 꾸미지 않습니다."],
        ["루프", "4", "세 편을 따로 쓰기", "첫 글은 이동 중심, 둘째는 일정 중심처럼 제목과 흐름이 겹치지 않게 만듭니다."],
        ["결과 검수", "5", "저장 전 살펴보기", "세 편이 모두 있는지, 제목과 소제목이 빠지지 않았는지, 서로 너무 닮지 않았는지 봅니다."],
        ["프로필 배정", "6", "원고와 블로그 짝짓기", "원고 1은 프로필 1, 원고 2는 프로필 2, 원고 3은 프로필 3으로 보냅니다."],
        ["업로드", "7", "임시저장 누르기", "검수를 통과한 글만 각 네이버 글쓰기 화면에 넣고 임시저장합니다."],
        ["실행 기록", "8", "끝난 일을 알려 주기", "어느 프로필까지 저장됐는지, 어디서 멈췄는지 사용자에게 그대로 알립니다."],
    ], [39 * mm, 12 * mm, 33 * mm, 90 * mm])]
    story += [Spacer(1, 4 * mm), p("문서는 이 흐름을 안내하는 참고 자료입니다. 실제 작업은 요청 파악, 자료 조사, 원고 작성, 결과 검수, 저장 순서로 진행됩니다.", "small")]
    story += [PageBreak()]

    # 3
    story += section("3. 공통 프롬프트는 글투, 스킬은 작업 순서입니다", "둘을 섞어 생각하면 문서가 더 어렵게 보입니다")
    story += [p("식당으로 치면 공통 프롬프트는 ‘우리 집 음식은 덜 짜게 만든다’는 조리 기준입니다. 스킬은 재료를 씻고, 조리하고, 접시에 담는 순서입니다. 하나는 결과물의 기준을 잡고, 다른 하나는 일을 진행하는 방법을 잡습니다.")]
    story += [flow([
        ("공통 프롬프트", "글투, 문단, 금지 표현을 정합니다"),
        ("내 말투 자료", "내가 직접 쓴 글만 말투에 참고합니다"),
        ("SKILL.md", "조사부터 저장까지 순서를 정합니다"),
        ("새 원고", "앞의 기준을 지켜 처음부터 새로 씁니다"),
    ], [43.5 * mm] * 4)]
    story += [Spacer(1, 5 * mm), card("공통 프롬프트에 적는 말", "“초보자도 읽기 편하게 짧은 문장으로 써 줘. 확인하지 않은 가격이나 영업시간은 단정하지 말아 줘. 내가 직접 경험했다고 알려 준 내용이 아니라면 다녀온 것처럼 쓰지 마.”", MINT)]
    story += [Spacer(1, 5 * mm), p("SKILL.md에는 완성 문장이 들어 있지 않습니다. 어느 자료를 먼저 보고, 원고는 몇 개 만들고, 무엇을 검사한 다음 어느 프로필에 저장할지를 적어 둡니다.") , PageBreak()]

    # Common prompt full text
    story += section("실제 공통 프롬프트의 구성", "아래 일곱 줄이 모든 원고의 기본 방향을 정합니다")
    story += [p("공통.txt는 블로그마다 함께 쓰는 글쓰기 기준입니다. Codex는 원고를 쓰기 직전에 이 내용을 읽습니다. 여기서 말투나 형식을 바꾸면 이후에 만드는 글에도 새 기준이 이어집니다. 작업 지시가 헷갈리지 않도록 이모티콘은 넣지 않았습니다.")]
    full_prompt = (
        "# Mato 공통 글작성 규칙<br/><br/>"
        "- 완성된 글만 보여 줍니다. 본문에서 원문, 출처, 작성자를 언급하지 않습니다.<br/>"
        "- 확인하지 않은 방문 경험이나 날짜, 가격, 숫자, 인용문을 지어내지 않습니다.<br/>"
        "- 사용자가 자신의 글 URL로 경험을 제공했다면 그 사실만 자연스러운 1인칭으로 풀어냅니다.<br/>"
        "- 사용자가 제목을 정했다면 글자 하나도 바꾸지 않습니다.<br/>"
        "- 휴대전화에서 읽기 편하도록 문장을 짧게 나눕니다. Markdown 기호는 쓰지 않습니다.<br/>"
        "- Mato 형식인 제목을입력해주세요1:, 본문2:, ㅂㅂㅂ소제목을 그대로 지킵니다.<br/>"
        "- [image_N.jpg] 표시는 받은 순서와 자리를 유지합니다. 임의로 더하거나 빼지 않습니다."
    )
    prompt_box = Table([[p(full_prompt, "code")]], colWidths=[174 * mm])
    prompt_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.8, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 11),
        ("RIGHTPADDING", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    story += [Spacer(1, 4 * mm), prompt_box]
    story += [Spacer(1, 6 * mm), card("언제 이 기준이 들어갈까요?", "먼저 SKILL.md를 따라 자료를 정리합니다. 실제 문장을 쓰기 직전에는 공통 프롬프트를 불러와 말투와 형식을 맞춥니다. 다시 말해 스킬이 작업의 길을 잡고, 공통 프롬프트가 글의 모양을 잡습니다.", SKY)]
    story += [Spacer(1, 4 * mm), p("비밀번호, 쿠키, 개인 연락처처럼 남에게 보여서는 안 되는 정보는 공통.txt에 적지 않습니다.", "small"), PageBreak()]

    # Common prompt roles
    story += section("공통 프롬프트에서 특히 중요한 규칙", "각 규칙이 어떤 실수를 막는지 함께 확인합니다")
    story += [p("공통 프롬프트를 통째로 외울 필요는 없습니다. 각 규칙이 필요한 이유와 완성된 원고에서 확인할 부분을 연결해 두면 실제 작업에 적용하기 쉽습니다.")]
    story += [table([
        ["꼭 지킬 규칙", "이 규칙이 막는 사고", "완성된 글에서 볼 부분"],
        ["원문·출처·작성자를 본문에서 말하지 않기", "조사 메모가 독자용 글에 그대로 섞이는 일", "‘원문에 따르면’ 같은 말 없이 글 자체만으로 자연스럽게 읽힙니다."],
        ["모르는 경험·날짜·가격·숫자를 만들지 않기", "가 보지 않은 곳을 다녀온 척하거나 틀린 정보를 단정하는 일", "직접 겪었다는 표현은 사용자가 알려 준 경험에만 붙습니다."],
        ["사용자가 정한 제목은 그대로 두기", "검색어가 들어간 제목을 Codex가 마음대로 바꾸는 일", "띄어쓰기까지 사용자가 준 제목과 같습니다."],
        ["문장을 짧게 쓰고 Markdown 기호는 빼기", "휴대전화 화면에서 글이 답답해 보이는 일", "별표나 샵 기호 없이 문단과 소제목으로 읽힙니다."],
        ["Mato 제목·본문·소제목 형식 지키기", "업로더가 제목과 본문을 엉뚱한 곳에 넣는 일", "제목을입력해주세요1:, 본문2:, ㅂㅂㅂ 표식이 제자리에 있습니다."],
        ["사진 표식의 번호와 자리 지키기", "원고와 사진의 순서가 뒤바뀌는 일", "[image_1.jpg] 옆에 실제 image_1.jpg 파일이 함께 있습니다."],
    ], [55 * mm, 52 * mm, 67 * mm])]
    story += [Spacer(1, 5 * mm), flow([
        ("전체 프롬프트", "글쓰기 약속을 한곳에 모읍니다"),
        ("중요 규칙", "사실, 형식, 사진 기준을 확인합니다"),
        ("스킬", "규칙을 실제 작업 순서에 적용합니다"),
        ("검수", "완성된 글이 규칙에 맞는지 확인합니다"),
    ], [43.5 * mm] * 4)]
    story += [Spacer(1, 5 * mm), card("공통 프롬프트에는 이모티콘을 넣지 않았습니다", "공통 프롬프트는 블로그 독자가 아니라 Codex가 읽는 작업 지시입니다. 이모티콘을 제외하면 일반 문장과 [image_1.jpg] 같은 파일 표식을 더 명확하게 구분할 수 있습니다.", YELLOW)]
    story += [PageBreak()]

    # 4
    story += section("4. 원고가 세 편이라면 같은 작업을 세 번 반복합니다", "이러한 반복 작업을 프로그램에서는 ‘루프’라고 부릅니다")
    story += [p("마트에서 사과 세 개를 살 때는 사과마다 상태를 확인한 뒤 장바구니에 담습니다. 원고도 같은 방식으로 처리합니다. 첫 번째 원고를 작성하고 검수한 뒤, 두 번째와 세 번째 원고에도 같은 과정을 적용합니다.")]
    story += [flow([
        ("요청 확인", "이번 원고의 주제와 번호를 확인합니다"),
        ("자료 정리", "이번 글에 사용할 정보만 추립니다"),
        ("원고 작성", "앞 글과 다른 제목과 흐름으로 씁니다"),
        ("원고 검수", "형식, 중복, 사진 연결을 확인합니다"),
        ("프로필 저장", "정해진 프로필에 원고를 저장합니다"),
    ], [34.8 * mm] * 5)]
    story += [Spacer(1, 6 * mm), table([
        ["작업 도중 생긴 일", "그 자리에서 하는 일"],
        ["세 번째 원고에 소제목이 빠졌습니다", "저장을 잠시 멈추고 세 번째 원고부터 고칩니다."],
        ["프로필 2만 로그인이 풀렸습니다", "다른 계정으로 넘기지 않고 프로필 2의 로그인만 다시 잡습니다."],
        ["발행됐는지 화면에서 알 수 없습니다", "성공했다고 넘겨짚지 않고 ‘확인하지 못함’으로 남깁니다."],
    ], [55 * mm, 119 * mm])]
    story += [Spacer(1, 4 * mm), p("루프의 목적은 작업 속도를 높이는 데만 있지 않습니다. 원고마다 작성과 검수를 같은 순서로 반복해, 잘못된 글이 다른 블로그에 들어가는 실수를 줄입니다.", "small"), PageBreak()]

    # 5
    story += section("5. 네이버 계정마다 전용 크롬 프로필을 사용합니다", "프로필 1을 열면 항상 같은 네이버 계정으로 연결되어야 합니다")
    story += [p("집 열쇠와 사무실 열쇠가 서로 다른 문을 여는 것처럼, 크롬 프로필도 계정마다 사용하는 공간이 정해져 있습니다. 각 프로필은 쿠키와 로그인 정보를 별도 폴더에 보관하므로 프로필 1과 프로필 2의 네이버 계정이 섞이지 않습니다.")]
    story += [table([
        ["번호", "프로필 이름", "처음 등록할 블로그 주소", "이 프로필이 여는 곳"],
        ["1", "Blog_1", "https://blog.naver.com/blog_1", "blog_1 블로그의 글쓰기 화면"],
        ["2", "Blog_2", "https://blog.naver.com/blog_2", "blog_2 블로그의 글쓰기 화면"],
        ["3", "Blog_3", "https://blog.naver.com/blog_3", "blog_3 블로그의 글쓰기 화면"],
    ], [16 * mm, 30 * mm, 61 * mm, 67 * mm])]
    story += [Spacer(1, 5 * mm), card("처음 만들 때는 사용자가 직접 로그인합니다", "프로필 번호와 Blog_1 같은 이름, 블로그 주소를 먼저 등록합니다. 전용 크롬 창이 열리면 사용자가 네이버에 로그인하고 글쓰기 화면까지 이동합니다. 창을 닫아도 로그인에 필요한 브라우저 정보는 프로필 폴더에 남습니다. 다음 작업에서는 같은 폴더를 다시 사용하며, 네이버가 세션을 만료시킨 경우에만 다시 로그인합니다.", SKY)]
    story += [Spacer(1, 5 * mm), p("프로필을 만들 때 보낼 요청", "h2"), p("프로필 1, 2, 3을 만들어줘. 1번은 Blog_1과 https://blog.naver.com/blog_1, 2번은 Blog_2와 https://blog.naver.com/blog_2, 3번은 Blog_3과 https://blog.naver.com/blog_3으로 설정해줘.", "code"), PageBreak()]

    # 6
    story += section("6. 자료를 가져오는 방법은 두 가지입니다", "키워드 검색과 내 글 URL은 이미지 처리 범위가 다릅니다")
    story += [p("‘서울 데이트 코스’를 검색해서 원고를 만드는 일과 사용자가 자신의 글 URL을 주고 재가공을 맡기는 일은 서로 다른 작업입니다. 글을 참고하는 범위와 이미지를 내려받을 수 있는 조건도 다릅니다.")]
    story += [table([
        ["입력 방법", "글은 어떻게 처리하나", "이미지는 어떻게 처리하나"],
        ["키워드 검색<br/>블로그탭·통합검색", "상위 글 여러 편에서 검색 의도, 반복되는 정보, 독자가 궁금해하는 내용을 정리합니다. 다른 사람의 문장과 경험담은 가져오지 않습니다.", "원문 이미지는 내려받지 않습니다. 사진이 들어갈 위치는 구상할 수 있지만, 실제 JPEG가 없다면 최종 원고에 [image_N.jpg] 태그를 넣지 않습니다."],
        ["내 글 URL", "사용자가 ‘내 글’이라고 확인한 URL의 내용과 경험을 새 구성으로 재가공합니다. 업로드 전에 원고와 이미지 연결 상태를 검수합니다.", "사용자 소유 이미지이므로 내려받을 수 있습니다. image_1.jpg부터 순서대로 저장하고 같은 번호의 [image_N.jpg] 태그를 원고에 연결합니다."],
    ], [37 * mm, 69 * mm, 68 * mm])]
    story += [Spacer(1, 5 * mm), card("A. 키워드 검색으로 새 원고 만들기", "‘서울 데이트 코스’를 블로그탭이나 통합검색에서 조사합니다. 여러 글의 공통 정보만 정리해 서로 다른 원고를 새로 씁니다. 검색한 글의 이미지는 내려받거나 업로드하지 않습니다.", YELLOW)]
    story += [Spacer(1, 4 * mm), card("B. 내 글 URL을 재가공해 업로드 대기하기", "사용자가 자신의 글이라고 확인한 URL이라면 글과 이미지를 함께 가져올 수 있습니다. 원고를 새 구성으로 다듬고, 이미지는 순서대로 저장해 태그와 연결합니다. 검수를 마친 뒤 사용자가 지정한 프로필에서 업로드 직전 상태로 대기합니다.", MINT)]
    story += [Spacer(1, 4 * mm), p("어느 경우든 원고에 [image_1.jpg] 태그가 있다면 같은 폴더에 실제 image_1.jpg 파일이 있어야 합니다. 이미지 파일 없이 태그만 만들지는 않습니다.", "small"), PageBreak()]

    # 7
    story += section("7. 원고 일곱 편을 프로필 세 개에 순서대로 배정합니다", "1번, 2번, 3번까지 배정한 뒤 다시 1번부터 시작합니다")
    story += [p("원고는 번호 순서대로 각 프로필에 배정합니다. 이 방식을 ‘라운드로빈’이라고 합니다. 원고 1은 프로필 1, 원고 2는 프로필 2, 원고 3은 프로필 3으로 보내고, 원고 4부터 같은 순서를 반복합니다.")]
    story += [table([
        ["원고", "배정 프로필", "블로그"],
        ["원고 1", "프로필 1", "Blog_1"],
        ["원고 2", "프로필 2", "Blog_2"],
        ["원고 3", "프로필 3", "Blog_3"],
        ["원고 4", "프로필 1", "Blog_1"],
        ["원고 5", "프로필 2", "Blog_2"],
        ["원고 6", "프로필 3", "Blog_3"],
        ["원고 7", "프로필 1", "Blog_1"],
    ], [42 * mm, 55 * mm, 77 * mm])]
    story += [Spacer(1, 5 * mm), card("여러 프로필의 크롬 창을 동시에 조작하지 않는 이유", "원고 수와 크롬 창 수는 같지 않습니다. 이 예시에서는 원고 일곱 편을 프로필 세 개에 나누므로 크롬 창도 세 개를 사용합니다. 네이버 편집기에서 사진 업로드와 임시저장을 동시에 진행하면 원고나 사진이 다른 계정에 들어갈 수 있습니다. 따라서 한 프로필의 저장 완료를 확인한 뒤 다음 프로필로 넘어갑니다.", MINT)]
    story += [Spacer(1, 5 * mm), p("프로필 2의 로그인이 풀리면 해당 작업을 중단합니다. 남은 원고를 프로필 1이나 3으로 임의 배정하지 않습니다. 프로필 2에 다시 로그인한 뒤 원래 배정된 원고부터 이어서 작업합니다.") , PageBreak()]

    # 8
    story += section("8. 저장하기 전에 원고를 최종 검수합니다", "글의 완성도와 함께 사용자의 요청이 정확히 반영됐는지 확인합니다")
    story += [p("원고 검수는 맞춤법만 보는 일이 아닙니다. 일곱 편을 부탁했는데 여섯 편만 있지는 않은지, 제목이 비어 있지는 않은지, 사진 표식과 실제 파일이 맞는지까지 함께 살핍니다.")]
    story += [table([
        ["검수할 부분", "여기서 잡아내는 실수"],
        ["원고 수", "일곱 편을 부탁했는데 여섯 편만 만들어진 경우"],
        ["제목과 본문 표식", "제목 또는 본문이 비어 있어 업로더가 넣을 내용을 찾지 못하는 경우"],
        ["소제목", "처음부터 끝까지 한 덩어리라 휴대전화에서 읽기 힘든 경우"],
        ["제목 겹침", "서로 다른 블로그에 사실상 같은 제목이 들어가는 경우"],
        ["원고끼리 닮은 정도", "단어 몇 개만 바꾸고 내용과 순서는 똑같은 경우"],
        ["사진 파일", "[image_1.jpg] 표시는 있는데 실제 사진이 폴더에 없는 경우"],
    ], [53 * mm, 121 * mm])]
    story += [Spacer(1, 5 * mm), card("임시저장과 발행은 결과가 다릅니다", "임시저장은 네이버에 초안을 보관하는 기능이므로 다른 사람에게 공개되지 않습니다. 발행하면 글이 공개됩니다. 따라서 사용자가 분명히 ‘발행해 줘’라고 요청하지 않았다면 임시저장까지만 진행합니다.", YELLOW)]
    story += [Spacer(1, 4 * mm), p("CAPTCHA가 뜨거나 로그인이 풀렸거나 저장 완료 표시가 보이지 않는다면 억지로 다음 단계로 넘기지 않습니다. 멈춘 위치와 화면 상태를 그대로 알립니다.", "small"), PageBreak()]

    # 9
    story += section("9. 질문에 포함된 정보에 따라 답변이 달라집니다", "부산역에서 남산타워까지 가는 길을 묻는 두 문장으로 차이를 확인합니다")
    story += [p("AI는 우리가 쓴 문장을 통째로 한 번에 읽지 않습니다. 문장을 작은 조각으로 잘라 숫자로 바꾼 뒤 읽습니다. 이 조각을 ‘토큰’이라고 부릅니다. 토큰은 글자 수나 띄어쓰기 단위와 딱 맞지 않아서, 같은 한국어 단어도 모델에 따라 여러 조각으로 나뉠 수 있습니다.")]
    story += [card("A. 친구에게 말하듯 쓴 문장", "부산역에서 여자친구랑 데이트하기 위해 서울 남산타워에 갈 건데 어떻게 가야 해?", SKY)]
    story += [Spacer(1, 4 * mm), card("B. 검색어처럼 줄인 문장", "부산역 서울 남산타워 가는 방법 대중교통", YELLOW)]
    story += [Spacer(1, 5 * mm), table([
        ["비교할 부분", "A 문장", "B 문장"],
        ["사용자 문장만 계산한 예상 토큰 수", "대략 25~45개", "대략 12~25개"],
        ["문장 안에 이미 들어 있는 단서", "부산역, 남산타워, 이동 방법, 여자친구와 가는 데이트", "부산역, 남산타워, 대중교통"],
        ["AI가 바로 파악할 수 있는 내용", "단순 이동이 아니라 데이트 동선이 필요하다는 점", "두 장소 사이의 대중교통 경로가 필요하다는 점"],
        ["추가로 필요한 정보", "출발 날짜와 시각, 원하는 답변 길이", "누구와 왜 가는지, 어느 정도로 자세히 알려 줄지"],
    ], [55 * mm, 59.5 * mm, 59.5 * mm])]
    story += [Spacer(1, 5 * mm), p("여기 적은 토큰 수는 원리를 보여 주기 위한 범위입니다. 실제 숫자는 어떤 모델을 쓰는지에 따라 달라집니다. 또 실제 작업에서는 이 한 문장뿐 아니라 공통 프롬프트, 앞선 대화, 검색 결과까지 입력으로 들어갑니다.", "small"), PageBreak()]

    # 10
    story += section("10. AI가 문장을 읽고 답변을 만드는 과정", "신경망은 문장 속 단어 사이의 관계를 계산합니다")
    story += [p("‘부산역’만 보면 AI는 장소 이름 하나를 받은 셈입니다. 여기에 ‘남산타워’, ‘대중교통’, ‘가는 방법’이 함께 들어오면 출발지와 목적지, 이동 수단을 묻는 문장으로 연결합니다. 이런 연결을 수많은 숫자 계산으로 처리하는 장치가 신경망입니다.")]
    story += [flow([
        ("1. 문장 자르기", "문장을 토큰이라는 조각으로 나눕니다"),
        ("2. 숫자로 옮기기", "각 조각을 계산할 수 있게 바꿉니다"),
        ("3. 서로 연결하기", "출발지와 목적지의 관계를 잡습니다"),
        ("4. 다음 조각 고르기", "답에 이어질 가능성이 큰 토큰을 고릅니다"),
        ("5. 답을 이어 붙이기", "문장이 끝날 때까지 같은 계산을 되풀이합니다"),
    ], [34.8 * mm] * 5)]
    story += [Spacer(1, 5 * mm), table([
        ["처리 단계", "남산타워 길찾기 질문의 예"],
        ["문장을 잘게 나눕니다", "‘부산역’, ‘서울’, ‘남산타워’, ‘대중교통’, ‘가는 방법’처럼 뜻을 다루기 좋은 조각으로 나눕니다."],
        ["각 조각을 숫자로 바꿉니다", "컴퓨터는 단어를 그대로 계산할 수 없으므로 긴 숫자 묶음으로 옮깁니다. 이 숫자 묶음을 벡터라고 합니다."],
        ["문장에서 중요한 관계를 찾습니다", "‘부산역’은 출발지, ‘남산타워’는 목적지, ‘대중교통’은 이동 조건으로 연결합니다. 이때 쓰이는 핵심 계산이 어텐션입니다."],
        ["답을 한 조각씩 만듭니다", "지금까지 읽은 내용 뒤에 ‘먼저’, ‘KTX를 타고’, ‘서울역에서’ 가운데 무엇이 자연스러운지 확률로 비교하며 이어 씁니다."],
    ], [43 * mm, 131 * mm])]
    story += [Spacer(1, 5 * mm), card("신경망이 최신 시간표까지 알고 있다는 뜻은 아닙니다", "신경망은 문맥에 맞는 답을 만드는 계산 방식입니다. 오늘 KTX가 몇 시에 출발하는지, 버스 노선이 바뀌었는지는 별도로 확인해야 합니다. 시간, 요금, 운행 여부처럼 자주 바뀌는 내용은 코레일이나 교통기관의 최신 정보를 찾아 답에 보탭니다.", YELLOW)]
    story += [PageBreak()]

    # 11
    story += section("11. 토큰은 질문과 답에서 모두 쓰입니다", "짧게 묻는다고 전체 사용량까지 꼭 줄어드는 것은 아닙니다")
    story += [p("AI가 읽은 분량을 입력 토큰, AI가 새로 써 낸 분량을 출력 토큰이라고 부릅니다. 여기서 입력은 방금 보낸 한 문장만 뜻하지 않습니다. 공통 프롬프트와 앞선 대화, 검색해서 가져온 글도 같은 입력에 포함됩니다.")]
    story += [table([
        ["어디에서 쓰이나", "함께 세는 내용", "분량이 커지는 예"],
        ["입력 토큰", "사용자 요청, 공통 프롬프트, 앞선 대화, 검색 자료, 도구가 돌려준 결과", "참고 글 다섯 편을 통째로 읽히거나 대화가 아주 길어진 경우"],
        ["출력 토큰", "AI가 새로 만든 답변, 블로그 원고, 검수 결과", "이동 경로를 자세히 설명하거나 긴 원고 세 편을 한꺼번에 쓴 경우"],
        ["추가 대화", "AI가 되묻는 질문과 사용자가 다시 보낸 답", "출발 시각을 묻고 답한 뒤 처음부터 경로를 다시 짜는 경우"],
    ], [35 * mm, 67 * mm, 72 * mm])]
    story += [Spacer(1, 5 * mm), card("B 문장이 짧아도 전체 토큰 사용량은 늘어날 수 있습니다", "A 문장은 조금 길지만 ‘여자친구와 가는 데이트’라는 상황까지 한 번에 전달합니다. B 문장은 짧은 대신 AI가 “언제 가나요?”, “경로만 필요한가요?”라고 되물을 수 있습니다. A가 입력 35토큰과 답변 400토큰으로 끝나는 동안, B는 입력 18토큰 뒤에 확인 질문과 추가 답변이 이어질 수 있습니다. 숫자는 원리를 설명하기 위한 예시이며 실제 사용량과는 다릅니다.", MINT)]
    story += [Spacer(1, 5 * mm), card("처음부터 이 정도로 말해 주면 좋습니다", "부산역에서 서울 남산타워까지 대중교통으로 가는 길을 알려줘. 여자친구와 당일 데이트로 갈 거야. KTX를 탄 뒤 지하철이나 버스로 어떻게 갈아타는지 순서대로 적어 주고, 환승 장소도 알려줘. 시간표처럼 바뀔 수 있는 내용은 최신 확인이 필요하다고 표시해줘.", SKY)]
    story += [Spacer(1, 5 * mm), p("목표는 질문을 무조건 짧게 줄이는 것이 아닙니다. 어디에서 어디로 가는지, 왜 가는지, 어떤 모양의 답을 원하는지 처음부터 알려 주면 되묻는 횟수와 재작업이 줄어듭니다. 그만큼 전체 토큰도 아낄 수 있습니다.") , PageBreak()]

    # 12
    story += section("12. 실제 작업에 바로 쓰는 요청 문장", "명령어 대신 필요한 내용을 평소 말투로 적어 보세요")
    examples = [
        "프로필 1을 만들어줘. 별칭은 Blog_1이고 블로그 URL은 https://blog.naver.com/blog_1 이야.",
        "프로필 1, 2, 3의 로그인 상태와 각 글쓰기 URL을 확인해줘.",
        "‘서울 데이트 코스’ 블로그탭 상위 글을 분석해서 서로 다른 원고 3개를 만들어줘. 아직 업로드하지는 마.",
        "이 URL을 참고해서 원고 7개를 만들고 프로필 1, 2, 3에 순서대로 임시저장해줘.",
        "방금 만든 원고가 서로 너무 비슷한지 검증해줘. 문제 있으면 업로드하지 말고 고쳐줘.",
        "원고 3개를 프로필 1, 2, 3에 발행해줘. 공개하기 전에 프로필과 원고 배정을 다시 확인해줘.",
    ]
    for index, text in enumerate(examples, start=1):
        story.append(KeepTogether([
            p(f"예시 {index}", "h2"),
            Table([[p(text, "code")]], colWidths=[174 * mm], style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7FAFC")),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ])),
        ]))
    story += [PageBreak()]

    # 13
    story += section("13. 첫 작업 전 확인할 다섯 가지", "앞에서 배운 내용을 실제 작업 순서에 맞춰 정리합니다")
    story += [card("1. README는 처음 준비할 때 확인합니다", "설치와 프로필 만들기가 끝났다면 글을 쓸 때마다 README를 다시 펼칠 필요는 없습니다. 실제 블로그 작업에서는 SKILL.md에 적힌 순서를 따릅니다.", SKY)]
    story += [Spacer(1, 3 * mm), card("2. 프로필은 네이버 계정마다 하나씩 사용합니다", "프로필 1·2·3은 각자 다른 크롬 폴더를 사용합니다. 같은 프로필을 다시 열어야 이전 로그인 상태도 이어집니다.", MINT)]
    story += [Spacer(1, 3 * mm), card("3. 원고는 프로필 번호 순서대로 배정합니다", "프로필 1, 2, 3을 선택했다면 원고 1은 1번, 원고 2는 2번, 원고 3은 3번, 원고 4는 다시 1번에 배정합니다.", SKY)]
    story += [Spacer(1, 3 * mm), card("4. 저장하기 전에는 원고와 사진을 함께 검수합니다", "원고 수, 제목, 소제목, 글끼리 닮은 정도, 사진 파일을 확인합니다. 저장 완료 여부가 확실하지 않다면 성공했다고 기록하지 않습니다.", YELLOW)]
    story += [Spacer(1, 3 * mm), card("5. 요청할 때는 네 가지를 또렷하게 적습니다", "무슨 글을 쓸지, 몇 편이 필요한지, 어느 프로필을 쓸지, 임시저장과 발행 중 어디까지 할지를 한 문장에 넣습니다.", MINT)]
    story += [Spacer(1, 7 * mm), p("두 가지 입력 방법을 구분한 요청 예시", "h2")]
    story += [table([
        ["입력 방법", "요청 문장"],
        ["키워드 검색", "‘서울 데이트 코스’를 블로그탭과 통합검색에서 조사해 서로 다른 원고 3개를 만들어줘. 검색 결과의 이미지는 내려받지 말고, 아직 업로드하지도 마."],
        ["내 글 URL", "이 URL은 내가 쓴 글이야. 글과 이미지를 내려받아 새 구성으로 재가공하고, 이미지 파일과 [image_N.jpg] 태그를 순서대로 연결해줘. 프로필 1에서 업로드 직전 상태로 대기해줘."],
    ], [35 * mm, 139 * mm])]

    doc.build(story)
    print(OUTPUT)


if __name__ == "__main__":
    build()
