# PowerPoint 原生公式

公式存为 `a14:m → m:oMathPara → m:oMath`，在 PowerPoint 的公式编辑器中编辑；不嵌入截图、OLE 或普通文本冒充公式。转换采用 LaTeX → MathML → DrawingML 版 OMML，字体为 Cambria Math，运行属性是 `a:rPr`，不混入 Word 的 `w:rPr`。实现依据 [Microsoft Office Math 扩展](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-odrawxml/853b19c7-68a9-4f9a-a2ae-5e6cb0d02e62)。

## 原子操作

```json
{"presentation_id":"deck","operation_id":"eq-1","base_revision":3,"slide_id":"method","operation":"element.add_equation","element":{"id":"integral","latex":"\\int_0^1 x^2\\,dx=\\frac{1}{3}","geometry":{"x":0.12,"y":0.3,"w":0.76,"h":0.35},"font_size":28,"align":"center"}}
```

保存请求到 `tmp/equation.json`，运行 `office_pptx.py apply-one files/deck.pptx --request tmp/equation.json`。使用实际 presentation_id、slide_id 和当前 base_revision。font_size 可省略，默认继承 style（默认 body）的字号；也可用已定义 anchor 定位。几何为幻灯片比例，必须留足分式、矩阵和积分上下限的高度。字号不会为塞入框中而自动缩小。

- 内容：`element.update`，`element:{"id":"integral","latex":"\\int_0^2 x^2\\,dx"}`。
- 样式：`element.set_style`，`element:{"id":"integral","style":{"font_size":30,"color":"accent","align":"left"}}`。color 接收色板 token 或 RRGGBB，align 为 left/center/right。
- 移动：`element.update` 的 geometry，或 `element.adjust` 的相对位移；仅改变该公式框。
- `slot.fill` 和 `element.update text` 不能覆盖原生公式。公式不使用文本 preset 或普通字体替换。

## Python 生成脚本

脚本从技能 scripts 导入 native_math，安装依赖见技能 requirements.txt（新增 latex2mathml）。无需本地 TeX、网络公式服务或 API key。

```python
from pptx import Presentation
from pptx.util import Inches
import deck_io
import native_math

prs = Presentation()
slide = prs.slides.add_slide(prs.slide_layouts[6])
eq = native_math.create(slide, r'A=\begin{bmatrix}a&b\\c&d\end{bmatrix}',
                        (Inches(1), Inches(1), Inches(6), Inches(2)),
                        font_size=28, color='17324D', align='center')
eq.name = 'da:matrix'  # Stable element ID, used for local edits/checks.
deck_io.save_deck(prs, 'files/deck.pptx')

# Repair an existing file by locating the exact slide/element; do not rebuild it.
prs = Presentation('files/deck.pptx')
eq = next(shape for shape in prs.slides[0].shapes if shape.name == 'da:matrix')
native_math.replace(eq, r'A=\begin{bmatrix}1&0\\0&1\end{bmatrix}',
                    font_size=28, color='17324D')
deck_io.save_deck(prs, 'files/deck.pptx')  # Write back in place; never keep a copy in tmp/.
```

`replace` preserves the shape ID, name, geometry and other slide objects; its explicit style arguments replace formula styling (defaults: 24 pt, 17324D, centered). Keep using the deck's shared design parameters when passing them.

## 支持范围

| 结构 | LaTeX 示例 |
| --- | --- |
| 分式、嵌套分式 | `\frac{a+b}{c}`、`\dfrac{1}{1+\frac{1}{x}}` |
| 矩阵 | `\begin{bmatrix}a&b\\c&d\end{bmatrix}`；matrix、pmatrix、Bmatrix、vmatrix、Vmatrix |
| 积分、重积分、上下限 | `\int_0^1 x^2\,dx`、`\iint_D f(x,y)\,dx\,dy` |
| 上下标、根式 | `x_i^2`、`\sqrt{x}`、`\sqrt[3]{y}` |
| 求和、连乘 | `\sum_{i=1}^n x_i`、`\prod_{i=1}^n x_i` |
| 希腊字母、运算符、函数 | `\alpha+\beta`、`\sin x`、`\lim_{x\to0}` |
| 分段函数、可伸缩括号、重音 | cases 环境、`\left(\frac{a}{b}\right)`、`\hat{x}`、`\vec{x}` |

输入为 LaTeX 数学主体，也接受外围 `$…$` / `$$…$$` / `\(…\)` / `\[…\]`。JSON 中反斜杠需要转义；Python 推荐 r 字符串。矩阵必须等列，最多 12×12；单公式最多 8000 字符。整篇 TeX、自定义宏、外部包、aligned/align 多行环境和任意 XML 输入不支持，会明确报错。多行推导可使用多个独立原生公式框并保持对齐，不能声称支持完整 LaTeX。

## 预览与检查

即时预览使用消毒后的 MathML；正式预览使用 LibreOffice。LibreOffice 会跳过段落内 MC 包装，因此仅在临时渲染副本展开公式 Choice，保持 OMML 本体不变，原始 PPT/版本快照不变。检查会发现公式区域没有渲染文本的情况；公式框同样参与遮挡检测。LibreOffice 的数学字体回退、缩放和排版可能与 PowerPoint 有差异，最终编辑和显示需在 PowerPoint 核对；不能将本地渲染描述为 PowerPoint 实机验收。
