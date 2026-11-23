import sys, re
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib import colors
F="/usr/share/fonts/truetype/dejavu/"
pdfmetrics.registerFont(TTFont("S",F+"DejaVuSerif.ttf")); pdfmetrics.registerFont(TTFont("SB",F+"DejaVuSerif-Bold.ttf"))
from reportlab.pdfbase.pdfmetrics import registerFontFamily
registerFontFamily("S",normal="S",bold="SB",italic="S",boldItalic="SB")
brown=colors.HexColor("#4a2c22"); gold=colors.HexColor("#a67c2e")
body=ParagraphStyle("b",fontName="S",fontSize=10,leading=14,alignment=TA_JUSTIFY,spaceAfter=4,textColor=colors.black)
bul=ParagraphStyle("u",parent=body,leftIndent=14,bulletIndent=4)
h1=ParagraphStyle("h1",fontName="SB",fontSize=15,leading=20,alignment=TA_CENTER,textColor=brown,spaceBefore=6,spaceAfter=4)
h1s=ParagraphStyle("h1s",fontName="S",fontSize=11,leading=15,alignment=TA_CENTER,textColor=brown,spaceAfter=10)
h2=ParagraphStyle("h2",fontName="SB",fontSize=11.5,leading=16,textColor=gold,spaceBefore=10,spaceAfter=5)
rk=ParagraphStyle("rk",parent=body,alignment=0)
src,out,edr=sys.argv[1:4]
REKV=f"""<b>Реквізити Виконавця:</b><br/>Фізична особа-підприємець<br/>Василенко Наталія Михайлівна<br/>РНОКПП (ЄДРПОУ) {edr}<br/>Адреса: Україна, 09108, м. Біла Церква, вул. Пантелеймона Куліша, 9, кв. 2<br/>E-mail: nat1972v@gmail.com"""
def esc(s): return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
story=[]
for line in open(src,encoding="utf-8").read().split("\n"):
    line=line.strip()
    if not line: continue
    line=line.replace("@EDRPOU",edr)
    for k,v in [("@P38","Виконавця"),("@P612","6.13"),("@P82","Не гарантуючи")]:
        line=line.replace(k,v)
    if line=="@REKV":
        story+= [Spacer(1,10),KeepTogether([Paragraph(REKV,rk)])]; continue
    t=esc(line)
    t=re.sub(r"\*\*(.+?)\*\*",r"<b>\1</b>",t)
    if line.startswith("#s "): story.append(Paragraph(t[3:],h1s))
    elif line.startswith("# "): story.append(Paragraph(t[2:],h1))
    elif line.startswith("## "): story.append(Paragraph(t[3:],h2))
    elif line.startswith("- "): story.append(Paragraph(t[2:],bul,bulletText="•"))
    else:
        t=re.sub(r"^(\d+\.\d+\.)",r"<b>\1</b>",t); story.append(Paragraph(t,body))
def foot(c,d):
    c.saveState(); c.setFont("S",8); c.setFillColor(colors.grey)
    c.drawCentredString(A4[0]/2,10*mm,f"ФОП Василенко Наталія Михайлівна · сторінка {d.page}"); c.restoreState()
SimpleDocTemplate(out,pagesize=A4,leftMargin=20*mm,rightMargin=20*mm,topMargin=18*mm,bottomMargin=18*mm,
  title=sys.argv[4],author="ФОП Василенко Наталія Михайлівна").build(story,onFirstPage=foot,onLaterPages=foot)
