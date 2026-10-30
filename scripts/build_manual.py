"""Generate the editable TeX manual from maintained user/developer documents."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent


def tex(text):
    text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text).replace("`", "").replace("**", "")
    replacements = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}", "≤": r"$\leq$", "≥": r"$\geq$"}
    result = "".join(replacements.get(c, c) for c in text)
    result = re.sub(r"[a-f0-9]{24,}", lambda m: r"\allowbreak{}".join(m.group()[i:i+8] for i in range(0,len(m.group()),8)), result)
    result = re.sub(r"[A-Za-z][A-Za-z0-9]{17,}", lambda m: r"\allowbreak{}".join(m.group()[i:i+8] for i in range(0,len(m.group()),8)), result)
    return result.replace("--", "-{}-").replace(r"\_", r"\_\allowbreak{}").replace("/", r"/\allowbreak{}")


def render(markdown):
    lines = markdown.splitlines()
    out, i, code = [], 0, False
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            code = not code
            out.append(r"\begin{quote}\small" if code else r"\end{quote}")
        elif line.startswith("|") and not code:
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip("|").split("|")]
                if not all(re.fullmatch(r"[-: ]+", c) for c in cells):
                    rows.append(cells)
                i += 1
            count = len(rows[0]); width = (15.5 - 0.5 * count) / count
            out.append(r"\begin{longtable}{" + "".join("p{" + str(round(width, 2)) + "cm}" for _ in range(count)) + "}")
            out.append(r"\toprule")
            for j, cells in enumerate(rows):
                out.append(" & ".join(tex(c) for c in cells) + r" \\")
                if j == 0:
                    out.append(r"\midrule\endhead")
            out.extend([r"\bottomrule", r"\end{longtable}"])
            continue
        elif line.startswith("# "):
            out.append(r"\section{" + tex(line[2:]) + "}")
        elif line.startswith("## "):
            out.append(r"\subsection{" + tex(line[3:]) + "}")
        elif line.startswith("- "):
            out.append(r"\noindent\textbullet\quad " + tex(line[2:]) + "\n\n")
        elif line.strip():
            out.append(tex(line) + (r"\par" if code else "\n"))
        else:
            out.append("\n")
        i += 1
    return "\n".join(out)


documents = ["README.md", "docs/FOR_AGENTS_INSTALL.md", "docs/USER_GUIDE.md", "docs/MEMORY_REUSE.md", "docs/DEVELOPMENT.md", "docs/RECOVERY.md", "docs/ACCEPTANCE.md", "docs/ASSET_PROVENANCE.md", "docs/PROGRESS.md", "docs/VALIDATION.md"]
source = r"""\documentclass[11pt,a4paper]{ctexart}
\usepackage[margin=22mm]{geometry}
\usepackage{longtable,booktabs,array,xcolor,hyperref}
\hypersetup{colorlinks=true,linkcolor=blue,urlcolor=blue,pdftitle={Auto-LAMMPS MCP 项目开发与交付手册},pdfauthor={Auto-LAMMPS}}
\setlength{\parskip}{0.5em}
\setlength{\parindent}{0pt}
\emergencystretch=3em
\begin{document}
\title{Auto-LAMMPS MCP\\项目开发与交付手册}
\author{Auto-LAMMPS}
\date{2026年10月30日}
\maketitle
从安装提示词进入现有 Agent 平台，完成科研执行、持续监视、分析和交接。
\tableofcontents
\clearpage
"""
source += "\n\\clearpage\n".join(render((ROOT / path).read_text()) for path in documents)
source += "\n\\end{document}\n"
(ROOT / "docs/manual/main.tex").write_text(source)
print("Generated docs/manual/main.tex")
