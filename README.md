Sudoku Pro PWA
================

Sudoku Pro is a professional-grade Progressive Web App (PWA) Sudoku game.
It is fully offline-capable, installable, and suitable for hosting on GitHub Pages
or wrapping into a mobile app (iOS / Android).

--------------------------------------------------
FEATURES
--------------------------------------------------
- Classic 9x9 Sudoku
- Multiple difficulty levels
- Notes (Pencil) mode
- Undo / Redo
- Hint system
- Game timer
- Local statistics
- Light & Dark mode
- Offline support (Service Worker)
- Installable PWA (Add to Home Screen)

--------------------------------------------------
TECH STACK
--------------------------------------------------
- HTML5
- CSS3
- Vanilla JavaScript
- Progressive Web App (PWA)
- Service Workers
- LocalStorage for persistence

--------------------------------------------------
PROJECT STRUCTURE
--------------------------------------------------
index.html          -> Main entry point
manifest.json       -> PWA manifest
service-worker.js   -> Offline caching

css/
  app.css           -> Core styles
  themes.css        -> Dark / Light themes

js/
  app.js            -> App bootstrap
  engine.js         -> Game logic, undo/redo
  generator.js      -> Sudoku generator
  solver.js         -> Solver + hint engine
  ui.js             -> UI rendering
  stats.js          -> Game statistics
  store.js          -> Local storage helpers

assets/
  icons/            -> PWA icons

--------------------------------------------------
DEPLOYMENT (GITHUB PAGES)
--------------------------------------------------
1. Upload all files to a GitHub repository root
2. Go to Settings -> Pages
3. Select branch: main
4. Select folder: /root
5. Save and access the generated URL

--------------------------------------------------
INSTALL AS APP
--------------------------------------------------
Mobile:
- Open the site in Safari or Chrome
- Tap 'Add to Home Screen'

Desktop:
- Open in Chrome
- Click Install icon in address bar

--------------------------------------------------
LICENSE
--------------------------------------------------
You are free to use, modify, and extend this project for
personal or commercial use.

--------------------------------------------------
AUTHOR
MKS
