"""Render the revised bridging-ranker pipeline as an editable one-slide .pptx.

Every node is a native rounded-rectangle shape and every arrow a native
connector, so stakeholders can move, recolour, or relabel anything in
PowerPoint / Keynote / Google Slides.

Design choices (documented for the project log):
- Subject is an ML feed-ranking pipeline -> calm, infrastructural palette:
  navy sources, two-step teal for data + representations, slate for the
  optional content models, amber for retrieval, deep teal for the merge /
  rank spine, and a single coral accent reserved for the final feed.
- One accent colour only, used once, so the eye lands on the output.
- Fixed node height and a 7-row grid keep it readable at a glance.
- Follower graph now descends from its own NETWORK SNAPSHOT source
  (left), decoupled from NEW POSTS INGESTED.
Run: uv run --with python-pptx python build_pptx.py
"""

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn

OUT = "bridging_ranker_pipeline.pptx"

# ---- palette -----------------------------------------------------------------
NAVY      = RGBColor(0x1C, 0x3F, 0x5F)
TEAL_D    = RGBColor(0x2E, 0x7D, 0x8C)
TEAL_L    = RGBColor(0x9B, 0xC7, 0xCE)
SLATE     = RGBColor(0x5C, 0x6F, 0x82)
AMBER     = RGBColor(0xD9, 0x8A, 0x3D)
DEEPTEAL  = RGBColor(0x23, 0x5F, 0x6B)
CORAL     = RGBColor(0xE8, 0x5D, 0x5D)
BG        = RGBColor(0xF5, 0xF3, 0xEF)
LINE      = RGBColor(0x8A, 0x98, 0xA2)
INK       = RGBColor(0x22, 0x30, 0x3A)
WHITE     = RGBColor(0xFF, 0xFF, 0xFF)

# ---- canvas ----------------------------------------------------------------
prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank

bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
bg.fill.solid(); bg.fill.fore_color.rgb = BG
bg.line.fill.background(); bg.shadow.inherit = False


def _txt(tf, lines, size, color, bold):
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    for m in ("left", "right", "top", "bottom"):
        setattr(tf, f"margin_{m}", Pt(2))
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run(); r.text = line
        r.font.size = Pt(size); r.font.bold = bold
        r.font.color.rgb = color; r.font.name = "Arial"


def box(cx, cy, w, h, fill, text, size=10, color=WHITE, bold=False, radius=0.14):
    x, y = Inches(cx - w / 2), Inches(cy - h / 2)
    s = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, Inches(w), Inches(h))
    try:
        s.adjustments[0] = radius
    except Exception:
        pass
    s.fill.solid(); s.fill.fore_color.rgb = fill
    s.line.color.rgb = WHITE; s.line.width = Pt(0.75)
    s.shadow.inherit = False
    _txt(s.text_frame, text.split("\n"), size, color, bold)
    s._geom = (cx, cy, w, h)
    return s


def edge(shape, side):
    cx, cy, w, h = shape._geom
    return {
        "t": (cx, cy - h / 2), "b": (cx, cy + h / 2),
        "l": (cx - w / 2, cy), "r": (cx + w / 2, cy),
    }[side]


def _arrowize(conn):
    ln = conn.line._get_or_add_ln()
    ln.set("w", str(Pt(1.5)))
    end = ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"})
    ln.append(end)


def arrow(p1, p2):
    c = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Inches(p1[0]), Inches(p1[1]), Inches(p2[0]), Inches(p2[1])
    )
    c.line.color.rgb = LINE; c.line.width = Pt(1.5)
    c.shadow.inherit = False
    _arrowize(c)
    return c


def elbow(start, corner, end):
    """Two-segment L-route; arrowhead on the final segment."""
    a = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Inches(start[0]), Inches(start[1]), Inches(corner[0]), Inches(corner[1])
    )
    a.line.color.rgb = LINE; a.line.width = Pt(1.5); a.shadow.inherit = False
    arrow(corner, end)


def label(cx, cy, text):
    t = slide.shapes.add_textbox(Inches(cx - 1.1), Inches(cy - 0.16), Inches(2.2), Inches(0.32))
    tf = t.text_frame; tf.word_wrap = False
    p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    r = p.add_run(); r.text = text
    r.font.size = Pt(8); r.font.italic = True
    r.font.color.rgb = SLATE; r.font.name = "Arial"


# ---- title -----------------------------------------------------------------
tt = slide.shapes.add_textbox(Inches(0.5), Inches(0.12), Inches(12.3), Inches(0.5))
_txt(tt.text_frame, ["Bridging Ranker — For-You Pipeline"], 18, INK, True)
tt.text_frame.paragraphs[0].alignment = PP_ALIGN.LEFT
tt.text_frame.paragraphs[0].runs[0].font.name = "Arial"
st = slide.shapes.add_textbox(Inches(0.52), Inches(0.56), Inches(12.3), Inches(0.32))
_txt(st.text_frame, ["Follower graph sourced from a dedicated network snapshot"], 10, SLATE, False)
st.text_frame.paragraphs[0].alignment = PP_ALIGN.LEFT

# ---- rows (vertical centres, inches) -------------------------------------
Y1, Y2, Y3, Y4, Y5, Y6, Y7 = 1.20, 2.25, 3.30, 4.40, 5.45, 6.30, 7.08
H = 0.60

# ---- row 1: sources ------------------------------------------------------
net   = box(1.95, Y1, 3.0, H, NAVY, "NETWORK SNAPSHOT", 11, WHITE, True)
posts = box(8.60, Y1, 8.4, H, NAVY, "NEW POSTS INGESTED", 11, WHITE, True)

# ---- row 2: raw data ----------------------------------------------------
fg   = box(1.95, Y2, 3.0, H, TEAL_D, "Follower graph", 10)
inter= box(5.15, Y2, 3.2, H, TEAL_D, "Interaction snapshots\n(likes · reposts)", 9)
cont = box(10.7, Y2, 4.5, H, TEAL_D, "Content\n(texts · URLs)", 9)

# ---- row 3: representations / models -----------------------------------
semb = box(1.95, Y3, 3.0, H, TEAL_L, "Structural embedding  zᶜ_u", 9, INK)
lat  = box(5.15, Y3, 3.2, H, TEAL_L, "Latent representation", 10, INK)
topic= box(9.35, Y3, 2.3, H, SLATE, "Topic model", 9.5)
har  = box(11.8, Y3, 2.3, H, SLATE, "Harm (HaR) model", 9.5)

# ---- row 4: retrieval -------------------------------------------------
brid = box(3.15, Y4, 3.7, H, AMBER, "Bridging Retriever · top-K", 10.5, WHITE, True)
relr = box(6.85, Y4, 3.4, H, AMBER, "Relevance Retriever · top-K", 10.5, WHITE, True)

# ---- row 5-7: merge, rank, feed ------------------------------------
cand = box(5.0, Y5, 6.6, H, DEEPTEAL, "Candidate set  —  union · dedup · topic diversification", 10.5, WHITE, True)
rank = box(5.0, Y6, 6.6, H, DEEPTEAL, "Ranker  —  recency · low harm · bridging", 10.5, WHITE, True)
feed = box(5.0, Y7, 4.3, 0.7, CORAL, "Final Top-K Feed", 14, WHITE, True)

# ---- connectors -----------------------------------------------------
arrow(edge(net, "b"), edge(fg, "t"))
arrow(edge(fg, "b"), edge(semb, "t"))
arrow((5.15, edge(posts, "b")[1]), edge(inter, "t"))
arrow((10.7, edge(posts, "b")[1]), edge(cont, "t"))
arrow(edge(inter, "b"), edge(lat, "t"))
arrow((9.35, edge(cont, "b")[1]), edge(topic, "t"))
arrow((11.8, edge(cont, "b")[1]), edge(har, "t"))

arrow(edge(semb, "b"), edge(brid, "t")); label(2.35, (Y3 + Y4) / 2, "structural")
arrow((4.6, Y3 + H / 2), (3.7, Y4 - H / 2)); label(4.35, (Y3 + Y4) / 2 + 0.02, "engagement")
arrow((5.7, Y3 + H / 2), (6.4, Y4 - H / 2)); label(6.15, (Y3 + Y4) / 2 + 0.02, "relevance")

arrow(edge(brid, "b"), (4.2, Y5 - H / 2)); label(3.5, (Y4 + Y5) / 2, "top-K_bridge")
arrow(edge(relr, "b"), (5.9, Y5 - H / 2)); label(6.4, (Y4 + Y5) / 2, "top-K_rel")

# topic diversification feed: down the right, into candidate set
elbow(edge(topic, "b"), (9.35, Y5), (edge(cand, "r")[0], Y5)); label(9.35, (Y4 + Y5) / 2 - 0.1, "topic diversify")
# HaR score into ranker
elbow(edge(har, "b"), (11.8, Y6), (edge(rank, "r")[0], Y6)); label(11.8, (Y4 + Y6) / 2, "HaR score")

arrow(edge(cand, "b"), edge(rank, "t"))
arrow(edge(rank, "b"), edge(feed, "t"))

prs.save(OUT)
print("wrote", OUT)
