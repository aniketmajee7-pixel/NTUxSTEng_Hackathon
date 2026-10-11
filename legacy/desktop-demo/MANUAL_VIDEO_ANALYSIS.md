# Manual browsing video analysis

## Method and limits

The source recording is **223.325 seconds** long. I exported a compatible 1280×720 copy and inspected one frame at every whole second, then reopened full-resolution frames around privacy warnings, first scrolls, product-card hovers, reversals, carousel controls, tab revisits, and browser close. The table below accounts for every second from 000 through 223.

The source is a phone recording of a laptop screen rather than a direct screen capture. Camera movement and perspective make absolute pointer coordinates and wheel counts unreliable. Timing is accurate to about ±1 second; pointer positions and paths are recorded relative to the visible Chrome window.

## Measured behaviour used by the demo

- During active traversal, a new viewport or section appears about every **1–3 seconds**.
- Ordinary product sections are reviewed for roughly **3–5 seconds** when the content is visually distinct.
- A selected “interest” area is held for **9 seconds** on the AWS automotive card, followed by roughly **16 seconds** around its carousel controls. The demo uses a conservative **4–8-second** interest pause once per normal page so it retains this pattern without regularly stalling.
- Pointer travel starts faster and slows near the destination. Long routes are about **0.60–1.08 seconds**, medium routes **0.35–0.72 seconds**, and short local adjustments **0.20–0.42 seconds**, scaled by the run’s pace.
- Pointer routes are mostly shallow diagonals between the current content center and a visible card/control. They stay inside the browser content column. An occasional 2–6-pixel overshoot is followed by a short settle.
- Downward browsing is sustained until the lower page. One small up/down orientation correction may occur; the larger reversal happens near the bottom. This replaces repeated random direction changes.
- The recording clicks Chrome’s visible **+** button and existing tabs. The updated demo does the same when UI Automation exposes those controls, with keyboard shortcuts as a fallback.
- On a privacy warning, the pointer goes to **Advanced**, pauses over the newly revealed explanation/link, and uses a visible Continue/Proceed control when present. If none exists, it leaves for the next site. A resulting **404 Not Found** is treated as a failed page.
- The existing deliberate typing error and correction remains enabled. Spreadsheet formulas and paths never receive intentional errors.

## Second-by-second observation

| Second | Page/state | Scroll or page motion | Pointer/action observation |
|---:|---|---|---|
| 000 | Windows desktop | No page motion | Pointer near desktop center; launch is about to start. |
| 001 | Chrome startup / new tab | No scrolling | Chrome opens; pointer stays near browser chrome while the first tab settles. |
| 002 | Chrome startup / new tab | No scrolling | Chrome opens; pointer stays near browser chrome while the first tab settles. |
| 003 | Chrome startup / new tab | No scrolling | Chrome opens; pointer stays near browser chrome while the first tab settles. |
| 004 | Coinbase address entry | No scrolling | Address-bar typing continues at a visibly deliberate pace. |
| 005 | Coinbase address entry | No scrolling | Address-bar typing continues at a visibly deliberate pace. |
| 006 | Coinbase address entry | No scrolling | Address-bar typing continues at a visibly deliberate pace. |
| 007 | Coinbase address entry | No scrolling | Address-bar typing continues at a visibly deliberate pace. |
| 008 | Coinbase address entry | No scrolling | Address-bar typing continues at a visibly deliberate pace. |
| 009 | Coinbase privacy warning | Warning replaces loading page | Pointer is near the right-side Reload/error area. |
| 010 | Coinbase privacy warning | No scrolling | Pointer crosses the page toward Advanced in about one second. |
| 011 | Expanded Coinbase warning | Advanced content appears | Advanced has been clicked; pointer pauses near the revealed area. |
| 012 | Expanded Coinbase HSTS message | No scrolling | Pointer drifts down and rests over the explanatory/HSTS text while the page is assessed. |
| 013 | Expanded Coinbase HSTS message | No scrolling | Pointer drifts down and rests over the explanatory/HSTS text while the page is assessed. |
| 014 | Expanded Coinbase HSTS message | No scrolling | Pointer drifts down and rests over the explanatory/HSTS text while the page is assessed. |
| 015 | Expanded Coinbase HSTS message | No scrolling | Pointer drifts down and rests over the explanatory/HSTS text while the page is assessed. |
| 016 | Expanded Coinbase HSTS message | No scrolling | Pointer drifts down and rests over the explanatory/HSTS text while the page is assessed. |
| 017 | Leaving blocked Coinbase page | No scrolling | Pointer travels diagonally upward toward Chrome's + control. |
| 018 | New tab | No scrolling | A visible new tab opens; short orientation pause before the next URL. |
| 019 | New tab | No scrolling | A visible new tab opens; short orientation pause before the next URL. |
| 020 | New tab | No scrolling | A visible new tab opens; short orientation pause before the next URL. |
| 021 | Samsung address entry | No scrolling | Address-bar typing; pointer remains in the upper browser region. |
| 022 | Samsung address entry | No scrolling | Address-bar typing; pointer remains in the upper browser region. |
| 023 | Samsung address entry | No scrolling | Address-bar typing; pointer remains in the upper browser region. |
| 024 | Samsung address entry | No scrolling | Address-bar typing; pointer remains in the upper browser region. |
| 025 | Samsung address entry | No scrolling | Address-bar typing; pointer remains in the upper browser region. |
| 026 | Samsung address entry | No scrolling | Address-bar typing; pointer remains in the upper browser region. |
| 027 | Samsung homepage hero | Initial page load; no meaningful scroll yet | Pointer settles around the middle of the content while the hero and cards render. |
| 028 | Samsung homepage hero | Initial page load; no meaningful scroll yet | Pointer settles around the middle of the content while the hero and cards render. |
| 029 | Samsung homepage hero | Initial page load; no meaningful scroll yet | Pointer settles around the middle of the content while the hero and cards render. |
| 030 | Samsung homepage hero | Initial page load; no meaningful scroll yet | Pointer settles around the middle of the content while the hero and cards render. |
| 031 | Samsung homepage hero | Initial page load; no meaningful scroll yet | Pointer settles around the middle of the content while the hero and cards render. |
| 032 | Samsung homepage hero | Initial page load; no meaningful scroll yet | Pointer settles around the middle of the content while the hero and cards render. |
| 033 | Samsung homepage hero | Initial page load; no meaningful scroll yet | Pointer settles around the middle of the content while the hero and cards render. |
| 034 | Samsung homepage hero | Initial page load; no meaningful scroll yet | Pointer settles around the middle of the content while the hero and cards render. |
| 035 | Samsung product cards | First definite wheel-driven section change | Pointer is near the middle Galaxy Buds card; the hover reveals “Learn more”. |
| 036 | Samsung product grid | Downward movement every roughly 1–3 seconds | Pointer remains in the central content column and makes short lateral trips between cards. |
| 037 | Samsung product grid | Downward movement every roughly 1–3 seconds | Pointer remains in the central content column and makes short lateral trips between cards. |
| 038 | Samsung product grid | Downward movement every roughly 1–3 seconds | Pointer remains in the central content column and makes short lateral trips between cards. |
| 039 | Samsung product grid | Downward movement every roughly 1–3 seconds | Pointer remains in the central content column and makes short lateral trips between cards. |
| 040 | Samsung product grid | Downward movement every roughly 1–3 seconds | Pointer remains in the central content column and makes short lateral trips between cards. |
| 041 | Samsung product grid | Downward movement every roughly 1–3 seconds | Pointer remains in the central content column and makes short lateral trips between cards. |
| 042 | Samsung product grid | Downward movement every roughly 1–3 seconds | Pointer remains in the central content column and makes short lateral trips between cards. |
| 043 | Samsung product grid | Downward movement every roughly 1–3 seconds | Pointer remains in the central content column and makes short lateral trips between cards. |
| 044 | Samsung product grid | Downward movement every roughly 1–3 seconds | Pointer remains in the central content column and makes short lateral trips between cards. |
| 045 | Samsung product grid | Downward movement every roughly 1–3 seconds | Pointer remains in the central content column and makes short lateral trips between cards. |
| 046 | Samsung recommendations/stories | Continued downward section traversal | Pointer continues content-local hovers instead of moving to browser edges. |
| 047 | Samsung recommendations/stories | Continued downward section traversal | Pointer continues content-local hovers instead of moving to browser edges. |
| 048 | Samsung recommendations/stories | Continued downward section traversal | Pointer continues content-local hovers instead of moving to browser edges. |
| 049 | Samsung recommendations/stories | Continued downward section traversal | Pointer continues content-local hovers instead of moving to browser edges. |
| 050 | Samsung recommendations/stories | Continued downward section traversal | Pointer continues content-local hovers instead of moving to browser edges. |
| 051 | Samsung recommendations/stories | Continued downward section traversal | Pointer continues content-local hovers instead of moving to browser edges. |
| 052 | Samsung recommendations/stories | Continued downward section traversal | Pointer continues content-local hovers instead of moving to browser edges. |
| 053 | Samsung recommendations/stories | Continued downward section traversal | Pointer continues content-local hovers instead of moving to browser edges. |
| 054 | Samsung lower content/footer | Larger downward jumps toward the bottom | Pointer drifts from center toward the lower-right content area. |
| 055 | Samsung lower content/footer | Larger downward jumps toward the bottom | Pointer drifts from center toward the lower-right content area. |
| 056 | Samsung lower content/footer | Larger downward jumps toward the bottom | Pointer drifts from center toward the lower-right content area. |
| 057 | Samsung lower content/footer | Larger downward jumps toward the bottom | Pointer drifts from center toward the lower-right content area. |
| 058 | Samsung lower content/footer | Larger downward jumps toward the bottom | Pointer drifts from center toward the lower-right content area. |
| 059 | Samsung lower content/footer | Larger downward jumps toward the bottom | Pointer drifts from center toward the lower-right content area. |
| 060 | Samsung lower page | Start of quick upward reversal | Pointer is around the lower-right content area before the page jumps upward. |
| 061 | Samsung upward review | Two or more quicker upward reversals | Pointer stays in the page while previously seen sections return. |
| 062 | Samsung upward review | Two or more quicker upward reversals | Pointer stays in the page while previously seen sections return. |
| 063 | Samsung upward review | Two or more quicker upward reversals | Pointer stays in the page while previously seen sections return. |
| 064 | Chrome tab strip | No scrolling | Pointer moves to and clicks the visible + control. |
| 065 | Apple address entry/loading | No scrolling | Address-bar typing and bounded load wait. |
| 066 | Apple address entry/loading | No scrolling | Address-bar typing and bounded load wait. |
| 067 | Apple address entry/loading | No scrolling | Address-bar typing and bounded load wait. |
| 068 | Apple address entry/loading | No scrolling | Address-bar typing and bounded load wait. |
| 069 | Apple iPhone hero | Hero visible | Pointer is approximately at the content center (recorded frame about x=496, y=200 in the 1280×720 export). |
| 070 | Apple iPhone hero | Short initial review | Pointer is around the middle of the hero rather than over Buy/Shop controls. |
| 071 | Apple iPhone hero | Short initial review | Pointer is around the middle of the hero rather than over Buy/Shop controls. |
| 072 | Apple iPhone to MacBook Air | One brisk downward transition | Pointer stays near the right half of the product image/content. |
| 073 | Apple iPhone to MacBook Air | One brisk downward transition | Pointer stays near the right half of the product image/content. |
| 074 | Apple MacBook Air | Section stationary | Pointer is approximately center-right of the content; no direct path toward Buy. |
| 075 | Apple MacBook Air | Section held for about four seconds | Pointer rests near the central-right blank/content area. |
| 076 | Apple MacBook Air | Section held for about four seconds | Pointer rests near the central-right blank/content area. |
| 077 | Apple MacBook Air | Section held for about four seconds | Pointer rests near the central-right blank/content area. |
| 078 | Apple next product section | Downward transition followed by about five seconds of review | Pointer remains central and avoids purchase buttons. |
| 079 | Apple next product section | Downward transition followed by about five seconds of review | Pointer remains central and avoids purchase buttons. |
| 080 | Apple next product section | Downward transition followed by about five seconds of review | Pointer remains central and avoids purchase buttons. |
| 081 | Apple next product section | Downward transition followed by about five seconds of review | Pointer remains central and avoids purchase buttons. |
| 082 | Apple next product section | Downward transition followed by about five seconds of review | Pointer remains central and avoids purchase buttons. |
| 083 | Apple product grid | Grid enters view | Pointer is near the right card; scroll resumes after the section review. |
| 084 | Apple product-card grid | Progressive downward movement every 1–3 seconds | Pointer shifts between central cards with short hovers. |
| 085 | Apple product-card grid | Progressive downward movement every 1–3 seconds | Pointer shifts between central cards with short hovers. |
| 086 | Apple product-card grid | Progressive downward movement every 1–3 seconds | Pointer shifts between central cards with short hovers. |
| 087 | Apple product-card grid | Progressive downward movement every 1–3 seconds | Pointer shifts between central cards with short hovers. |
| 088 | Apple product-card grid | Progressive downward movement every 1–3 seconds | Pointer shifts between central cards with short hovers. |
| 089 | Apple product-card grid | Progressive downward movement every 1–3 seconds | Pointer shifts between central cards with short hovers. |
| 090 | Apple product-card grid | Progressive downward movement every 1–3 seconds | Pointer shifts between central cards with short hovers. |
| 091 | Apple product-card grid | Progressive downward movement every 1–3 seconds | Pointer shifts between central cards with short hovers. |
| 092 | Apple product-card grid | Progressive downward movement every 1–3 seconds | Pointer shifts between central cards with short hovers. |
| 093 | Apple product-card grid | Progressive downward movement every 1–3 seconds | Pointer shifts between central cards with short hovers. |
| 094 | Apple product-card grid | Progressive downward movement every 1–3 seconds | Pointer shifts between central cards with short hovers. |
| 095 | Apple product-card grid | Progressive downward movement every 1–3 seconds | Pointer shifts between central cards with short hovers. |
| 096 | Apple entertainment grid | Downward movement into media cards | Pointer remains over imagery/content, not account or purchase controls. |
| 097 | Apple entertainment grid | Downward movement into media cards | Pointer remains over imagery/content, not account or purchase controls. |
| 098 | Apple entertainment grid | Downward movement into media cards | Pointer remains over imagery/content, not account or purchase controls. |
| 099 | Apple entertainment grid | Downward movement into media cards | Pointer remains over imagery/content, not account or purchase controls. |
| 100 | Apple entertainment grid | Downward movement into media cards | Pointer remains over imagery/content, not account or purchase controls. |
| 101 | Apple entertainment grid | Downward movement into media cards | Pointer remains over imagery/content, not account or purchase controls. |
| 102 | Apple footer | Bottom content visible | Pointer stays over content while the scrollbar is near the bottom. |
| 103 | Apple footer/lower content | Bottom reached | Pointer pauses around lower-page text and footer content. |
| 104 | Apple upward review | Quick upward reversal | Several earlier entertainment cards return with shorter pauses. |
| 105 | Apple upward review | Quick upward reversal | Several earlier entertainment cards return with shorter pauses. |
| 106 | Apple upward review | Quick upward reversal | Several earlier entertainment cards return with shorter pauses. |
| 107 | Apple upward review | Quick upward reversal | Several earlier entertainment cards return with shorter pauses. |
| 108 | Apple retained content / cards | Small position changes and review | Pointer stays inside the content grid; an unsafe subpage action follows in the recording. |
| 109 | Apple retained content / cards | Small position changes and review | Pointer stays inside the content grid; an unsafe subpage action follows in the recording. |
| 110 | Apple retained content / cards | Small position changes and review | Pointer stays inside the content grid; an unsafe subpage action follows in the recording. |
| 111 | Apple retained content / cards | Small position changes and review | Pointer stays inside the content grid; an unsafe subpage action follows in the recording. |
| 112 | Apple retained content / cards | Small position changes and review | Pointer stays inside the content grid; an unsafe subpage action follows in the recording. |
| 113 | Apple retained content / cards | Small position changes and review | Pointer stays inside the content grid; an unsafe subpage action follows in the recording. |
| 114 | Apple retained content / cards | Small position changes and review | Pointer stays inside the content grid; an unsafe subpage action follows in the recording. |
| 115 | Apple subpage privacy warning | No scrolling | Pointer begins near the right-center error area. |
| 116 | Apple subpage privacy warning | No scrolling | Pointer begins near the right-center error area. |
| 117 | Apple privacy warning | No page motion | Pointer is visibly on Advanced after a leftward/downward trip. |
| 118 | Expanded Apple warning | Advanced text and Proceed link visible | Pointer travels down toward the revealed host-named Proceed link and inspects it. |
| 119 | Expanded Apple warning | Advanced text and Proceed link visible | Pointer travels down toward the revealed host-named Proceed link and inspects it. |
| 120 | Expanded Apple warning | Proceed link visible | Pointer is over or just above the host-named Proceed link. |
| 121 | 404 Not Found | Destination changed | The warning disappears but the destination is unusable; this is an error state to skip. |
| 122 | 404 Not Found after Proceed | Warning clears to an unusable destination | Pointer moves upward toward the tab strip rather than staying on the failed page. |
| 123 | Samsung retained tab | Tab switch completed | The previous Samsung page returns at its saved mid-page position. |
| 124 | Retained Samsung tab | Samsung returns without retyping/reload | Pointer clicks the earlier tab and briefly reviews its preserved scroll position. |
| 125 | Chrome tab strip | No scrolling | Pointer clicks + to create another visible tab. |
| 126 | AWS address entry | No scrolling | Address-bar typing continues at the same deliberate pace. |
| 127 | AWS address entry | No scrolling | Address-bar typing continues at the same deliberate pace. |
| 128 | AWS address entry | No scrolling | Address-bar typing continues at the same deliberate pace. |
| 129 | AWS address entry | No scrolling | Address-bar typing continues at the same deliberate pace. |
| 130 | AWS address entry | No scrolling | Address-bar typing continues at the same deliberate pace. |
| 131 | AWS address entry | No scrolling | Address-bar typing continues at the same deliberate pace. |
| 132 | AWS address entry | No scrolling | Address-bar typing continues at the same deliberate pace. |
| 133 | AWS address entry | No scrolling | Address-bar typing continues at the same deliberate pace. |
| 134 | AWS loading | No scrolling | Short bounded load transition. |
| 135 | AWS hero | Hero fully available | Pointer is above the main hero card, around the content center. |
| 136 | AWS hero | Small initial downward change near the end | Pointer rests near the central hero, then the first scroll begins. |
| 137 | AWS hero | Small initial downward change near the end | Pointer rests near the central hero, then the first scroll begins. |
| 138 | AWS hero | Small initial downward change near the end | Pointer rests near the central hero, then the first scroll begins. |
| 139 | AWS hero | Small initial downward change near the end | Pointer rests near the central hero, then the first scroll begins. |
| 140 | AWS hero | Small initial downward change near the end | Pointer rests near the central hero, then the first scroll begins. |
| 141 | AWS hero | Small initial downward change near the end | Pointer rests near the central hero, then the first scroll begins. |
| 142 | AWS introduction/customer-stories lead-in | Gradual downward traversal every 1–3 seconds | Pointer stays around center and follows the content vertically. |
| 143 | AWS introduction/customer-stories lead-in | Gradual downward traversal every 1–3 seconds | Pointer stays around center and follows the content vertically. |
| 144 | AWS introduction/customer-stories lead-in | Gradual downward traversal every 1–3 seconds | Pointer stays around center and follows the content vertically. |
| 145 | AWS introduction/customer-stories lead-in | Gradual downward traversal every 1–3 seconds | Pointer stays around center and follows the content vertically. |
| 146 | AWS introduction/customer-stories lead-in | Gradual downward traversal every 1–3 seconds | Pointer stays around center and follows the content vertically. |
| 147 | AWS introduction/customer-stories lead-in | Gradual downward traversal every 1–3 seconds | Pointer stays around center and follows the content vertically. |
| 148 | AWS introduction/customer-stories lead-in | Gradual downward traversal every 1–3 seconds | Pointer stays around center and follows the content vertically. |
| 149 | AWS introduction/customer-stories lead-in | Gradual downward traversal every 1–3 seconds | Pointer stays around center and follows the content vertically. |
| 150 | AWS introduction/customer-stories lead-in | Gradual downward traversal every 1–3 seconds | Pointer stays around center and follows the content vertically. |
| 151 | AWS introduction/customer-stories lead-in | Gradual downward traversal every 1–3 seconds | Pointer stays around center and follows the content vertically. |
| 152 | AWS automotive story | Car card enters view | Pointer is near the upper middle of the car image. |
| 153 | AWS automotive story card | Car card comes fully into view | Pointer rests over the image/content for a noticeably longer inspection. |
| 154 | AWS automotive story card | Car card comes fully into view | Pointer rests over the image/content for a noticeably longer inspection. |
| 155 | AWS automotive story card | Car card comes fully into view | Pointer rests over the image/content for a noticeably longer inspection. |
| 156 | AWS automotive story card | Car card comes fully into view | Pointer rests over the image/content for a noticeably longer inspection. |
| 157 | AWS automotive story card | Car card comes fully into view | Pointer rests over the image/content for a noticeably longer inspection. |
| 158 | AWS automotive story card | Car card comes fully into view | Pointer rests over the image/content for a noticeably longer inspection. |
| 159 | AWS carousel orientation | Small up/down corrections around the story card | Pointer explores the central card area rather than roaming the whole window. |
| 160 | AWS carousel orientation | Small up/down corrections around the story card | Pointer explores the central card area rather than roaming the whole window. |
| 161 | AWS carousel orientation | Small up/down corrections around the story card | Pointer explores the central card area rather than roaming the whole window. |
| 162 | AWS carousel orientation | Small up/down corrections around the story card | Pointer explores the central card area rather than roaming the whole window. |
| 163 | AWS carousel orientation | Small up/down corrections around the story card | Pointer explores the central card area rather than roaming the whole window. |
| 164 | AWS carousel orientation | Small up/down corrections around the story card | Pointer explores the central card area rather than roaming the whole window. |
| 165 | AWS automotive story | Long interest dwell begins | Pointer holds over the card with little visible movement. |
| 166 | AWS automotive story | Minimal page motion; roughly nine-second interest dwell | Pointer holds over the hero/card, showing a deliberate reading pause. |
| 167 | AWS automotive story | Minimal page motion; roughly nine-second interest dwell | Pointer holds over the hero/card, showing a deliberate reading pause. |
| 168 | AWS automotive story | Minimal page motion; roughly nine-second interest dwell | Pointer holds over the hero/card, showing a deliberate reading pause. |
| 169 | AWS automotive story | Minimal page motion; roughly nine-second interest dwell | Pointer holds over the hero/card, showing a deliberate reading pause. |
| 170 | AWS automotive story | Minimal page motion; roughly nine-second interest dwell | Pointer holds over the hero/card, showing a deliberate reading pause. |
| 171 | AWS automotive story | Minimal page motion; roughly nine-second interest dwell | Pointer holds over the hero/card, showing a deliberate reading pause. |
| 172 | AWS automotive story | Minimal page motion; roughly nine-second interest dwell | Pointer holds over the hero/card, showing a deliberate reading pause. |
| 173 | AWS automotive story | Minimal page motion; roughly nine-second interest dwell | Pointer holds over the hero/card, showing a deliberate reading pause. |
| 174 | AWS carousel area | Control exploration begins | Pointer starts moving toward the lower-center 1/5 arrow control. |
| 175 | AWS carousel controls | Minor vertical adjustments, then a long hold near 1/5 controls | Pointer travels toward the lower-center carousel arrows and lingers about sixteen seconds without clicking risky content. |
| 176 | AWS carousel controls | Minor vertical adjustments, then a long hold near 1/5 controls | Pointer travels toward the lower-center carousel arrows and lingers about sixteen seconds without clicking risky content. |
| 177 | AWS carousel controls | Minor vertical adjustments, then a long hold near 1/5 controls | Pointer travels toward the lower-center carousel arrows and lingers about sixteen seconds without clicking risky content. |
| 178 | AWS carousel controls | Minor vertical adjustments, then a long hold near 1/5 controls | Pointer travels toward the lower-center carousel arrows and lingers about sixteen seconds without clicking risky content. |
| 179 | AWS carousel controls | Minor vertical adjustments, then a long hold near 1/5 controls | Pointer travels toward the lower-center carousel arrows and lingers about sixteen seconds without clicking risky content. |
| 180 | AWS carousel controls | Minor vertical adjustments, then a long hold near 1/5 controls | Pointer travels toward the lower-center carousel arrows and lingers about sixteen seconds without clicking risky content. |
| 181 | AWS carousel controls | Minor vertical adjustments, then a long hold near 1/5 controls | Pointer travels toward the lower-center carousel arrows and lingers about sixteen seconds without clicking risky content. |
| 182 | AWS carousel controls | Minor vertical adjustments, then a long hold near 1/5 controls | Pointer travels toward the lower-center carousel arrows and lingers about sixteen seconds without clicking risky content. |
| 183 | AWS carousel controls | Minor vertical adjustments, then a long hold near 1/5 controls | Pointer travels toward the lower-center carousel arrows and lingers about sixteen seconds without clicking risky content. |
| 184 | AWS carousel area | No meaningful scroll | Pointer is close to the carousel arrows; the long control-area hover continues. |
| 185 | AWS carousel controls | Minor vertical adjustments, then a long hold near 1/5 controls | Pointer travels toward the lower-center carousel arrows and lingers about sixteen seconds without clicking risky content. |
| 186 | AWS carousel controls | Minor vertical adjustments, then a long hold near 1/5 controls | Pointer travels toward the lower-center carousel arrows and lingers about sixteen seconds without clicking risky content. |
| 187 | AWS carousel controls | Minor vertical adjustments, then a long hold near 1/5 controls | Pointer travels toward the lower-center carousel arrows and lingers about sixteen seconds without clicking risky content. |
| 188 | AWS carousel controls | Minor vertical adjustments, then a long hold near 1/5 controls | Pointer travels toward the lower-center carousel arrows and lingers about sixteen seconds without clicking risky content. |
| 189 | AWS carousel area | Long hold ends | Pointer remains near the arrow control immediately before downward browsing resumes. |
| 190 | AWS content below carousel | Downward transition resumes | Pointer leaves the carousel controls and returns to the central reading column. |
| 191 | AWS content below carousel | Downward transition resumes | Pointer leaves the carousel controls and returns to the central reading column. |
| 192 | AWS industry copy | Downward scrolling resumes | Pointer returns to the central reading column. |
| 193 | AWS industry section | One downward section change | Pointer sits around central text. |
| 194 | AWS industry section | One downward section change | Pointer sits around central text. |
| 195 | AWS loading/cards section | Continued downward movement | Pointer remains low-center while content placeholders/cards pass. |
| 196 | AWS loading/cards section | Continued downward movement | Pointer remains low-center while content placeholders/cards pass. |
| 197 | AWS loading/cards section | Continued downward movement | Pointer remains low-center while content placeholders/cards pass. |
| 198 | AWS Global Infrastructure | Downward steps with 2–3-second reviews | Pointer follows text and regional controls without activating them. |
| 199 | AWS Global Infrastructure | Downward steps with 2–3-second reviews | Pointer follows text and regional controls without activating them. |
| 200 | AWS Global Infrastructure | Downward steps with 2–3-second reviews | Pointer follows text and regional controls without activating them. |
| 201 | AWS Global Infrastructure | Downward steps with 2–3-second reviews | Pointer follows text and regional controls without activating them. |
| 202 | AWS Global Infrastructure | Downward steps with 2–3-second reviews | Pointer follows text and regional controls without activating them. |
| 203 | AWS Global Infrastructure | Downward steps with 2–3-second reviews | Pointer follows text and regional controls without activating them. |
| 204 | AWS region metrics | Further downward traversal | Pointer makes small local moves near plus icons and text but does not click purchase/account controls. |
| 205 | AWS region metrics | Further downward traversal | Pointer makes small local moves near plus icons and text but does not click purchase/account controls. |
| 206 | AWS region metrics | Further downward traversal | Pointer makes small local moves near plus icons and text but does not click purchase/account controls. |
| 207 | AWS region metrics | Further downward traversal | Pointer makes small local moves near plus icons and text but does not click purchase/account controls. |
| 208 | AWS region metrics | Further downward traversal | Pointer makes small local moves near plus icons and text but does not click purchase/account controls. |
| 209 | AWS region metrics | Further downward traversal | Pointer makes small local moves near plus icons and text but does not click purchase/account controls. |
| 210 | AWS region metrics | Further downward traversal | Pointer makes small local moves near plus icons and text but does not click purchase/account controls. |
| 211 | AWS free-trial/feedback area | Final downward steps | Pointer remains centered; feedback panel is reached. |
| 212 | AWS free-trial/feedback area | Final downward steps | Pointer remains centered; feedback panel is reached. |
| 213 | AWS free-trial/feedback area | Final downward steps | Pointer remains centered; feedback panel is reached. |
| 214 | AWS feedback panel | Last meaningful content section | Pointer pauses around the center of the gradient feedback panel. |
| 215 | AWS end-of-page/upward transition | Quick reversal and sparse/blank transition frames | Pointer heads toward browser chrome after the page review. |
| 216 | AWS end-of-page/upward transition | Quick reversal and sparse/blank transition frames | Pointer heads toward browser chrome after the page review. |
| 217 | AWS end-of-page/upward transition | Quick reversal and sparse/blank transition frames | Pointer heads toward browser chrome after the page review. |
| 218 | AWS end-of-page/upward transition | Quick reversal and sparse/blank transition frames | Pointer heads toward browser chrome after the page review. |
| 219 | Samsung retained tab | Visible tab revisit | The older Samsung tab is activated; its scroll position is preserved. |
| 220 | Retained Samsung tab | Earlier Samsung tab is clicked and restored | Pointer is near the tab strip; preserved mid-page position appears without retyping. |
| 221 | Retained Samsung tab | Earlier Samsung tab is clicked and restored | Pointer is near the tab strip; preserved mid-page position appears without retyping. |
| 222 | Windows desktop | Chrome window closes | Pointer is near desktop center after the browser closes. |
| 223 | Windows desktop | Chrome closes | Pointer returns to the desktop center and the browsing run ends. |

## Code mapping

These observations are implemented in:

- `gui_support.py`: distance-based pointer travel, shallow curved paths, occasional settle, faster wheel gaps, and shorter between-section dwell.
- `browser_action.py`: section-sized sustained downward passes without the former small down/up correction, a single longer interest hover, quick bottom review, visible new-tab/tab clicks, Advanced-then-Continue handling, HSTS inspection, and 404 recovery.
- `config.json`: ordinary read, interest read, and warning-inspection timing bands.

The Windows pilot remains the final source of truth for cursor geometry because Chrome UI Automation bounds, VM resolution, and page layouts can differ from the recording.
