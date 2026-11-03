"""Render the resume-level architecture as editable SVG and a vector PDF.

Uses only the bundled ReportLab runtime. Coordinates are top-down. This is a
logical capability diagram, not a claim of production deployment or four
independent Agent microservices.
"""

from __future__ import annotations

import math
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor


ROOT = Path(__file__).resolve().parent
PDF_ROOT = ROOT.parent / "pdf"
STEM = "AI健身平台_项目架构图_简历版_20261013"
WIDTH, HEIGHT = 1600, 1050
FONT = "ArchitectureCJK"
pdfmetrics.registerFont(
    TTFont(FONT, "/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
)
PDF_ROOT.mkdir(parents=True, exist_ok=True)
pdf_path = PDF_ROOT / f"{STEM}.pdf"
svg_path = ROOT / f"{STEM}.svg"
c = canvas.Canvas(str(pdf_path), pagesize=(WIDTH / 2, HEIGHT / 2))
c.setTitle("AI 健身平台项目架构图 - 简历版")
c.setAuthor("AI 健身平台")
c.scale(0.5, 0.5)
svg: list[str] = [
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title description">',
    '<title id="title">AI 健身平台四个核心子项目架构图</title>',
    '<desc id="description">权限 RAG 与个性化训练、智能课程预约、经营分析客服与主动服务共用多 Agent 编排和安全运行底座。Agent 经 Java Tool Gateway 读取 MySQL 业务事实，通过独立训练、预约和客服服务进行业务写入。预约与训练的 Outbox 事件通过 RabbitMQ 和 Inbox 进入主动服务。四个领域子图在同一 Agent 服务中。</desc>',
    '<rect width="1600" height="1050" fill="#ffffff"/>',
    '<g font-family="Arial Unicode MS, PingFang SC, Hiragino Sans GB, sans-serif">',
]
INK = "#303b43"
SECONDARY = "#58636c"
TEAL = "#50b7be"
GREEN = "#8dbd60"
SUPPORT = "#c9898c"
ASYNC = "#536f86"


def color(value: str) -> None:
    c.setStrokeColor(HexColor(value))
    c.setFillColor(HexColor(value))


def text(x: float, y: float, lines: list[str], size: float = 24,
         line_height: float | None = None, fill: str = INK,
         anchor: str = "middle") -> None:
    """Place a centered block; y is its center, not first baseline."""
    gap = line_height or size * 1.38
    c.setFont(FONT, size)
    c.setFillColor(HexColor(fill))
    for index, value in enumerate(lines):
        baseline = y + (index - (len(lines) - 1) / 2) * gap + size * 0.34
        if anchor == "start":
            c.drawString(x, HEIGHT - baseline, value)
        else:
            c.drawCentredString(x, HEIGHT - baseline, value)
        svg.append(f'<text x="{x}" y="{baseline}" text-anchor="{anchor}" font-size="{size}" fill="{fill}">{escape(value)}</text>')


def box(x: float, y: float, width: float, height: float,
        stroke: str = INK, radius: float = 18, fill: str = "#ffffff",
        line_width: float = 2.5) -> None:
    c.setStrokeColor(HexColor(stroke))
    c.setFillColor(HexColor(fill))
    c.setLineWidth(line_width)
    c.setDash()
    c.roundRect(x, HEIGHT - y - height, width, height, radius, stroke=1, fill=1)
    svg.append(f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="{line_width}"/>')


def ellipse(cx: float, cy: float, rx: float, ry: float) -> None:
    c.setStrokeColor(HexColor(INK))
    c.setFillColor(HexColor("#ffffff"))
    c.setLineWidth(2.3)
    c.setDash()
    c.ellipse(cx-rx, HEIGHT-cy-ry, cx+rx, HEIGHT-cy+ry, stroke=1, fill=1)
    svg.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="#ffffff" stroke="{INK}" stroke-width="2.3"/>')


def path(commands: list[tuple], stroke: str = INK, dashed: bool = False,
         arrow: bool = True, line_width: float = 2.4) -> None:
    p = c.beginPath()
    d: list[str] = []
    endpoints: list[tuple[float, float]] = []
    for command in commands:
        kind, *values = command
        d.append(kind + " " + " ".join(str(v) for v in values))
        if kind == "M":
            p.moveTo(values[0], HEIGHT-values[1])
            endpoints.append((values[0], values[1]))
        elif kind == "L":
            p.lineTo(values[0], HEIGHT-values[1])
            endpoints.append((values[0], values[1]))
        elif kind == "C":
            p.curveTo(values[0], HEIGHT-values[1], values[2], HEIGHT-values[3], values[4], HEIGHT-values[5])
            endpoints.extend([(values[2], values[3]), (values[4], values[5])])
    c.setStrokeColor(HexColor(stroke))
    c.setLineWidth(line_width)
    c.setDash([9, 7] if dashed else [])
    c.drawPath(p, stroke=1, fill=0)
    dash = ' stroke-dasharray="9 7"' if dashed else ""
    svg.append(f'<path d="{" ".join(d)}" fill="none" stroke="{stroke}" stroke-width="{line_width}" stroke-linejoin="round"{dash}/>')
    c.setDash()
    if arrow:
        end, prior = endpoints[-1], endpoints[-2]
        angle = math.atan2(end[1]-prior[1], end[0]-prior[0])
        length, half_width = 13, 5.8
        base = (end[0]-length*math.cos(angle), end[1]-length*math.sin(angle))
        vertices = [end,
                    (base[0]+half_width*math.sin(angle), base[1]-half_width*math.cos(angle)),
                    (base[0]-half_width*math.sin(angle), base[1]+half_width*math.cos(angle))]
        a = c.beginPath()
        a.moveTo(vertices[0][0], HEIGHT-vertices[0][1])
        for px, py in vertices[1:]:
            a.lineTo(px, HEIGHT-py)
        a.close()
        color(stroke)
        c.drawPath(a, stroke=0, fill=1)
        points = " ".join(f"{px},{py}" for px, py in vertices)
        svg.append(f'<polygon points="{points}" fill="{stroke}"/>')


def label(x: float, y: float, lines: list[str], size: float = 20,
          fill: str = SECONDARY) -> None:
    gap = size * 1.35
    width = max(pdfmetrics.stringWidth(value, FONT, size) for value in lines) + 16
    height = len(lines) * gap + 4
    c.setFillColor(HexColor("#ffffff"))
    c.rect(x-width/2, HEIGHT-y-height/2, width, height, fill=1, stroke=0)
    svg.append(f'<rect x="{x-width/2}" y="{y-height/2}" width="{width}" height="{height}" fill="#ffffff"/>')
    text(x, y, lines, size, gap, fill)


# Heading and a restrained legend, similar to the reference resume's schematic.
text(80, 43, ["AI 健身服务与门店运营平台"], 32, anchor="start")
text(80, 87, ["项目架构图：一个平台，四个核心子项目"], 21, fill=SECONDARY, anchor="start")
path([("M", 938, 48), ("L", 980, 48)], arrow=False, line_width=2)
text(990, 48, ["能力 / 数据流"], 17, fill=SECONDARY, anchor="start")
path([("M", 1150, 48), ("L", 1192, 48)], SUPPORT, True, False, 2)
text(1202, 48, ["共享底座"], 17, fill=SECONDARY, anchor="start")
path([("M", 1330, 48), ("L", 1372, 48)], ASYNC, True, False, 2)
text(1382, 48, ["异步事件"], 17, fill=SECONDARY, anchor="start")

# Connectors first, nodes above them. No Agent-to-MySQL arrow is drawn.
path([("M", 1150, 135), ("C", 1090, 135, 1090, 235, 995, 235)])
path([("M", 665, 235), ("L", 540, 235)])
path([("M", 830, 295), ("C", 830, 345, 1040, 345, 1100, 365)])
path([("M", 540, 255), ("C", 600, 255, 590, 440, 640, 450)])
path([("M", 540, 475), ("L", 640, 475)])
path([("M", 1100, 420), ("C", 1080, 420, 1070, 495, 1020, 495)])
path([("M", 830, 533), ("L", 830, 637.5)])
path([("M", 1020, 450), ("L", 1095, 450), ("L", 1095, 690), ("L", 1150, 690)])
path([("M", 1045, 705), ("L", 1158, 705)])
path([("M", 830, 742.5), ("L", 830, 805), ("L", 1565, 805), ("L", 1565, 390), ("L", 1540, 390)], ASYNC, True)

# The three resume capability projects share one runtime foundation.
path([("M", 80, 913), ("L", 55, 913), ("L", 55, 235), ("L", 100, 235)], SUPPORT, True)
path([("M", 320, 875), ("L", 320, 537)], SUPPORT, True)
path([("M", 1520, 913), ("L", 1585, 913), ("L", 1585, 340), ("L", 1540, 340)], SUPPORT, True)

# Main capability projects.
box(100, 173, 440, 124, TEAL, line_width=3)
text(320, 199, ["项目二"], 20, fill=SECONDARY)
text(320, 246, ["权限隔离 RAG", "与个性化训练系统"], 25, 33)
box(100, 413, 440, 124, GREEN, line_width=3)
text(320, 439, ["项目三"], 20, fill=SECONDARY)
text(320, 486, ["智能课程预约", "与可靠业务执行系统"], 25, 33)
box(1100, 315, 440, 150, TEAL, line_width=3)
text(1320, 341, ["项目四"], 20, fill=SECONDARY)
text(1320, 387, ["经营分析、智能客服", "与主动服务系统"], 25, 33)
text(1320, 437, ["固定指标 · 工单 · 主动提醒"], 18, fill=SECONDARY)

# Knowledge and business integration are represented at resume granularity.
ellipse(1320, 135, 170, 47)
text(1320, 135, ["健身知识 / 门店规则", "文档解析与 OCR"], 22, 29)
diamond = [(830, 175), (995, 235), (830, 295), (665, 235)]
dp = c.beginPath()
dp.moveTo(diamond[0][0], HEIGHT-diamond[0][1])
for x, y in diamond[1:]:
    dp.lineTo(x, HEIGHT-y)
dp.close()
c.setFillColor(HexColor("#ffffff"))
c.setStrokeColor(HexColor(INK))
c.setLineWidth(2.4)
c.drawPath(dp, stroke=1, fill=1)
svg.append('<polygon points="830,175 995,235 830,295 665,235" fill="#ffffff" stroke="#303b43" stroke-width="2.4"/>')
text(830, 235, ["权限 RAG 知识库", "混合检索 / RRF / 重排"], 21, 28)
box(640, 417, 380, 116, radius=12)
text(830, 475, ["Java Tool Gateway", "权限复核 / 固定业务接口"], 23, 33)
box(615, 637.5, 430, 105, radius=12)
text(830, 690, ["Spring Boot 业务服务", "训练 / 预约 / 客服"], 24, 34)
ellipse(1320, 690, 170, 47)
text(1320, 690, ["核心业务 MySQL", "课程 · 合同 · 预约 · 经营数据"], 20, 29)

# Relationship labels; keep labels off the project names and gateway blocks.
label(1100, 192, ["解析 / 入库"], 19)
label(602, 212, ["检索增强"], 19)
label(990, 325, ["规则检索"], 19)
label(580, 340, ["训练草案", "审核发布"], 18)
label(590, 500, ["确认后执行"], 17)
label(1110, 520, ["指标 / 工单"], 18)
label(943, 586, ["受控业务写入"], 19)
label(1095, 585, ["固定查询", "只读聚合"], 18)
label(1110, 750, ["事务读写"], 18)
label(1270, 805, ["Outbox → RabbitMQ → Inbox"], 21, ASYNC)
label(995, 841, ["预约 / 训练事件"], 18, ASYNC)
label(320, 720, ["统一编排与安全执行"], 20, SUPPORT)

# Long foundation bar mirrors the reference's shared-service bottom bar.
box(80, 875, 1440, 125, radius=6, line_width=2.7)
text(800, 901, ["项目一：多 Agent 编排与安全运行平台"], 27)
text(800, 941, ["FastAPI + LangGraph Supervisor（健身指导 / 预约 / 经营 / 客服四个领域子图）"], 22)
text(800, 977, ["模型网关 · Tool Registry · 用户确认 · Memory / Checkpoint · SSE · 运行保护 · OTel / TruLens / Prometheus"], 18)
text(800, 1027, ["Agent 数据：PostgreSQL / pgvector；协作状态：Redis。四个领域子图同属 Agent 服务，事件 Worker 独立运行。"], 17, fill=SECONDARY)

svg.extend(["</g>", "</svg>"])
svg_path.write_text("\n".join(svg) + "\n", encoding="utf-8")
c.showPage()
c.save()
print(pdf_path)
print(svg_path)
