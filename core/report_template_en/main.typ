#let report = json(bytes(sys.inputs.report))


//Einstellungen zum Text
#set text(
  lang: "en",
  font: "Inter",
  size: 11pt
)

#set par(
  spacing: 12pt
)

#set page(
  paper: "a4"
)

#set document(
  title: report.title
)

#set list(spacing: 10pt)

// Einstellungen zu den Überschriften
#set heading(numbering: "1.1  ")

  // Textgrößen von Überschriften
  #show heading.where(level: 1): set text(size: 14pt)
  #show heading.where(level: 2): set text(size: 12pt)
  #show heading.where(level: 3): set text(size: 12pt)

#show heading.where(level: 1): it => block(
  below: 1.8em,
  it
)

#show heading.where(level: 1): it => [
  #pagebreak(weak: true)
  #it
]

#show heading.where(level: 2): it => block(
  above: 36pt,
  below: 18pt,
  it
)

#show heading.where(level: 3): it => block(
  above: 36pt,
  below: 18pt,
  it
)

//Titel
#show title: set align(right)
#show title: set text(size: 20pt, weight: "bold")

//Tables
#set table(
  fill: (_, y) => if y == 0 {luma(82%)},
  stroke: 0.5pt
)

#show table.cell: set text(size: 9pt)
#show table.cell: set par(justify: false)
#show table.cell.where(y: 0): set text(weight: "bold")

#set outline.entry(fill: none)

#align(right)[#rect()[#text(size: 16pt)[*Report*]]]

#v(40pt)

#title()

#v(200pt)

#align(right)[Author: automatically generated report]

#v(180pt)

#align(right)[#text(size: 13pt)[*Date: #datetime.today().display("[day]. [month repr:long] [year]")*]]

#v(15pt)

#grid(
  columns: (1fr,auto),
  align: (right, left),
  image("pictures/pando_logo.png", width: 40%),
  text(size: 13pt)[
*PANDO* \
*Methods for the automated generation*\
*of travel demand models*]
)

// Header und Footer
#set page(
  header: context {
    if calc.odd(here().page()) {
    align(right, [#report.munincipality])
  } else {
    align(left, [#report.munincipality])
  }
  v(-8pt)
  line(length: 100%)
},
  footer: context [
    #line(length: 100%)
    #v(-4pt)
    #grid(columns: (1fr, 1fr), align: (left, right))[PANDO #datetime.today().display("[year]")][#counter(page).display()]
  ],
  numbering: "i"
)

#show outline.entry.where(level: 1): set block(above: 2.2em)
#show outline.entry.where(level: 1): set text(weight: "bold")
#show outline.entry.where(level: 2): set block(above: 1.3em)

#outline(
  title: [Contents],
  indent: 0em
)

#counter(page).update(1)

#pagebreak()

#set page(numbering: "1")
#counter(page).update(1)

#set par(
  justify: true,
)

//Einstellungen zu Grafiken
#set figure(gap: 16pt)
#show figure: set place(clearance: 3em)
#show image: set image(width: 100%)
#show figure: it => block(
  above: 18pt,
  below: 18pt,
  it
)
#show image: it => [
  #rect(stroke: 0.8pt, width: 100%)[#it]
]

//Captions
#show figure.caption: it => [
  #align(left)[
  #grid(
    columns: (70pt, auto),
    [#it.supplement #context it.counter.display(it.numbering):],
    [#it.body]
  )]
]

#include "text.typ"

= References

#bibliography("roth-generische-verkehrsmodelle.bib", title: none, style: "Zitierstile/VuV - Author-Date (Standard).csl")

= List of figures and tables

#show outline.entry.where(level: 1): set block(above: 1em)
#show outline.entry.where(level: 1): set text(weight: "regular")
#show outline.entry.where(level: 2): set block(above: 1em)

== Figures

#outline(
  target: figure.where(kind: image),
  title: none
)

== Tables

#outline(
  target: figure.where(kind: table),
  title: none
)
