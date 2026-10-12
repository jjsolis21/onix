"""
xlsx.py — Ónix FM
Escritor mínimo de libros de Excel (.xlsx) sin dependencias: varias hojas,
encabezado en negrita con color, anchos de columna, fila congelada y filtro.
Los números se guardan como números (Excel puede sumarlos) y el resto como texto.
"""

import io
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape

_CT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
       '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
       '<Default Extension="xml" ContentType="application/xml"/>'
       '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
       '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
       '{hojas}</Types>')

_STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
           '<font><b/><sz val="11"/><color rgb="FF000000"/><name val="Calibri"/></font></fonts>'
           '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
           '<fill><patternFill patternType="solid"><fgColor rgb="FFEE9B00"/><bgColor indexed="64"/></patternFill></fill></fills>'
           '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
           '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
           '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
           '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
           '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
           '</styleSheet>')


def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _celda(ref: str, valor, estilo: int = 0) -> str:
    st = f' s="{estilo}"' if estilo else ""
    if valor is None or valor == "":
        return f'<c r="{ref}"{st}/>'
    if isinstance(valor, bool):
        valor = "Sí" if valor else "No"
    if isinstance(valor, (int, float)):
        return f'<c r="{ref}"{st}><v>{valor}</v></c>'
    return f'<c r="{ref}"{st} t="inlineStr"><is><t xml:space="preserve">{escape(str(valor))}</t></is></c>'


def _hoja(columnas: list[str], filas: list[list]) -> str:
    anchos = [max(len(str(c)), *(len(str(f[i])) for f in filas[:300] if i < len(f))) if filas else len(str(c))
              for i, c in enumerate(columnas)]
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{min(60, max(8, a + 2))}" customWidth="1"/>'
                   for i, a in enumerate(anchos))
    filas_xml = ['<row r="1">' + "".join(_celda(f"{_col(i)}1", c, 1) for i, c in enumerate(columnas)) + "</row>"]
    for r, fila in enumerate(filas, start=2):
        filas_xml.append(f'<row r="{r}">' + "".join(_celda(f"{_col(i)}{r}", v) for i, v in enumerate(fila)) + "</row>")
    ultima = f"{_col(max(0, len(columnas) - 1))}{max(1, len(filas) + 1)}"
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            '</sheetView></sheetViews>'
            f'<cols>{cols}</cols><sheetData>{"".join(filas_xml)}</sheetData>'
            f'<autoFilter ref="A1:{ultima}"/></worksheet>')


def libro(hojas: list[tuple[str, list[str], list[list]]]) -> bytes:
    """hojas = [(nombre, [columnas], [[fila…], …]), …] → bytes del .xlsx"""
    nombres = []
    for n, _, _ in hojas:
        limpio = "".join(c for c in n if c not in '[]:*?/\\')[:31] or "Hoja"
        while limpio in nombres:
            limpio = limpio[:28] + f"_{len(nombres)}"
        nombres.append(limpio)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT.format(hojas="".join(
            f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(len(hojas)))))
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' +
                   "".join(f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(nombres)) +
                   "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
                   "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                           for i in range(len(hojas))) +
                   f'<Relationship Id="rId{len(hojas) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", _STYLES)
        for i, (_, cols, filas) in enumerate(hojas):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _hoja(cols, filas))
    return buf.getvalue()


def nombre_archivo(base: str) -> str:
    return f"{base}_{datetime.now():%Y-%m-%d_%H%M}.xlsx"
