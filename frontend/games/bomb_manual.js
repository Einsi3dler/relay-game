// BOMB DEFUSE — the manual, and the data the bomb and the manual share.
//
// Two screens read this file. The Defuser's own copy, flipped to from the bomb
// face at the cost of fuse; and the Grandmaster's console on the leader
// dashboard, which is the second seat the source game always had (bomb.md §3,
// docs/GAME_DESIGN.md §2c). One file so the two can never disagree about what
// a wall is — and because the *bomb's* rules mirror is built on this same data,
// a drift here would put the manual and the server's replay out of step, which
// tests/games/fixtures/bomb_defuse_cases.json exists to prevent.
//
// The manual is stateless: `render` takes a page and some callbacks, and draws
// into whatever host it is given at the game's 590x440 logical scale. It never
// knows whose screen it is on.
(function () {
  "use strict";

  var MAZE_SIZE = 4;
  var W = 590, H = 440;

  //   h[r][c] — wall between (r, c) and (r + 1, c);  v[r][c] — between (r, c) and (r, c + 1)
  var MAZE_LAYOUTS = [
    { tip: [0, 1], h: [[0,1,0,0],[0,0,1,0],[1,1,0,0]], v: [[1,0,0],[1,0,1],[0,1,1],[0,0,0]] },
    { tip: [0, 3], h: [[1,0,1,0],[0,1,1,0],[0,1,0,1]], v: [[0,1,0],[1,0,0],[0,1,0],[0,0,0]] },
    { tip: [1, 0], h: [[0,1,0,0],[1,1,1,0],[0,0,1,0]], v: [[1,0,0],[0,0,1],[0,0,1],[1,0,0]] },
    { tip: [1, 2], h: [[0,0,0,1],[0,1,1,0],[0,0,1,0]], v: [[1,0,0],[1,1,0],[1,0,0],[0,1,0]] },
    { tip: [2, 1], h: [[1,1,0,0],[0,0,1,0],[0,1,0,0]], v: [[0,0,1],[1,0,1],[0,1,0],[0,0,1]] },
    { tip: [2, 3], h: [[1,0,0,1],[0,0,1,0],[0,1,0,0]], v: [[0,1,0],[1,1,0],[1,0,1],[0,0,0]] },
    { tip: [3, 0], h: [[1,1,0,0],[0,1,1,0],[0,0,0,0]], v: [[0,0,1],[0,1,0],[1,0,1],[0,1,0]] },
    { tip: [3, 2], h: [[1,0,1,0],[0,1,1,0],[0,0,0,1]], v: [[0,1,0],[1,0,0],[0,1,0],[1,0,0]] }
  ];

  var SIMON_COLOURS = ["red", "blue", "green", "yellow"];
  var SIMON_MAP = { red: "blue", blue: "red", green: "yellow", yellow: "green" };

  var NUMBER_PATTERNS = [
    [[1,6,3],[8,2,4],[5,9,7]],
    [[5,7,9],[2,4,3],[6,8,1]],
    [[2,7,5],[4,3,6],[8,1,9]],
    [[5,3,9],[1,7,2],[8,6,4]],
    [[6,3,2],[8,5,4],[1,7,9]],
    [[8,2,4],[3,1,7],[6,9,5]],
    [[3,7,6],[4,8,1],[2,5,9]],
    [[6,1,4],[2,9,7],[3,8,5]]
  ];

  // §26 palette, kept as one table so a screenshot pass tunes it in one place.
  var C = {
    manualBg: "#fff5bc", black: "#000000", white: "#ffffff",
    panelGrey: "#cbcbcb", bombGreyDark: "#373737", bombGrey: "#666666",
    bombGreyLight: "#aeaeae", manualExitBlue: "#0e00a9", bgBlue: "#001777",
    shutter: "#ff6600", shutterDark: "#c95000", green: "#00ff02",
    cyan: "#00d4ff", red: "#ff0000", tip: "#17c40a"
  };

  var SIMON_PAINT = {
    red: ["#8b0000", "#ff3b30"], blue: ["#00308b", "#3b82ff"],
    green: ["#0a6b12", "#31d94a"], yellow: ["#8b7500", "#ffdd33"]
  };

  // Colour is never the only carrier of meaning: each pad wears a shape and its
  // name too, so the mapping is readable without seeing hue.
  var SIMON_SHAPE = { red: "▲", blue: "●", green: "■", yellow: "◆" };

  var MODULE_NAMES = {
    maze: "Maze", simon: "Simon Says",
    according_to_number: "According to number", mini_button: "The mini button"
  };

  // §29/§84: only the modules this version implements. No dead buttons for
  // Wires, Memory, Keypads or Read and Press — their behaviour was never
  // verified, so guessing at it would be inventing a different game.
  var PAGES = ["maze", "simon", "according_to_number", "mini_button"];

  // --- DOM helpers (shared with the bomb face) -----------------------------

  function el(tag, css, text) {
    var node = document.createElement(tag);
    if (css) node.style.cssText = css;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function at(node, x, y, w, h) {
    node.style.cssText += "position:absolute;left:" + x + "px;top:" + y + "px;" +
      "width:" + w + "px;height:" + h + "px;box-sizing:border-box;";
    return node;
  }

  function button(label, css, onClick) {
    var node = el("button", "font-family:Arial,Helvetica,sans-serif;cursor:pointer;" +
      "border-radius:0;padding:0;" + (css || ""), label);
    node.setAttribute("type", "button");
    node.addEventListener("click", function (event) {
      if (event && event.preventDefault) event.preventDefault();
      onClick(event);
    });
    return node;
  }

  function clear(node) {
    if (node) node.innerHTML = "";
  }

  // --- the pages (§68-§71) -------------------------------------------------

  function renderHome(body, options) {
    // §29: a heavy black-bordered grey selector.
    var withheld = options.withheld || [];
    var selector = at(el("div"), 0, 0, 330, 240);
    selector.style.cssText += "background:" + C.panelGrey + ";border:6px solid " + C.black + ";" +
      "padding:10px;display:flex;flex-direction:column;gap:8px;";
    PAGES.forEach(function (type) {
      if (withheld.indexOf(type) !== -1) {
        // A page this copy does not have. Not a disabled button — there is no
        // control here to press, and drawing one would send the Defuser
        // clicking at it while the fuse burns. It says who does have it.
        selector.appendChild(el("div",
          "border:3px dashed " + C.bombGrey + ";background:" + C.bombGreyLight + ";" +
          "color:" + C.bombGreyDark + ";font-size:15px;font-weight:700;height:46px;" +
          "display:flex;align-items:center;padding-left:12px;box-sizing:border-box;",
          MODULE_NAMES[type] + " — ask your Grandmaster"));
        return;
      }
      selector.appendChild(button(MODULE_NAMES[type],
        "border:3px solid " + C.black + ";background:" + C.white + ";color:" + C.black + ";" +
        "font-size:17px;font-weight:700;height:46px;text-align:left;padding-left:12px;",
        function () { options.onNavigate(type); }));
    });
    body.appendChild(selector);
    body.appendChild(el("div", "position:absolute;left:346px;top:0;width:200px;font-size:13px;" +
      "line-height:1.5;", options.homeNote));
  }

  function renderMaze(body) {
    body.appendChild(el("div", "position:absolute;left:0;top:0;width:550px;font-size:13px;" +
      "line-height:1.45;",
      "Blue is the Defuser's position; red is the way out. Green is the tip " +
      "that identifies which maze is on the bomb — it is a label, not a target."));
    var size = 70, cell = size / 4;
    MAZE_LAYOUTS.forEach(function (layout, index) {
      var x = (index % 4) * (size + 62) + 4;
      var y = Math.floor(index / 4) * (size + 44) + 42;
      var box = el("div", "position:absolute;left:" + x + "px;top:" + y + "px;width:" + size +
        "px;height:" + size + "px;border:2px solid " + C.black + ";background:" + C.white + ";");
      // Walls in red (§38), drawn straight from the shared layout data.
      for (var r = 0; r < MAZE_SIZE; r++) {
        for (var c = 0; c < MAZE_SIZE; c++) {
          if (r < MAZE_SIZE - 1 && layout.h[r][c]) {
            box.appendChild(el("div", "position:absolute;left:" + (c * cell) + "px;top:" +
              ((r + 1) * cell - 1) + "px;width:" + cell + "px;height:2px;background:" + C.red + ";"));
          }
          if (c < MAZE_SIZE - 1 && layout.v[r][c]) {
            box.appendChild(el("div", "position:absolute;left:" + ((c + 1) * cell - 1) + "px;top:" +
              (r * cell) + "px;width:2px;height:" + cell + "px;background:" + C.red + ";"));
          }
        }
      }
      // A filled circle, where every wall is a straight line: the tip reads as
      // the tip without relying on telling green from red.
      box.appendChild(el("div", "position:absolute;left:" + (layout.tip[1] * cell + 3) + "px;top:" +
        (layout.tip[0] * cell + 3) + "px;width:" + (cell - 6) + "px;height:" + (cell - 6) +
        "px;background:" + C.tip + ";border-radius:50%;"));
      body.appendChild(box);
    });
  }

  function swatch(colour) {
    return el("div", "width:38px;height:28px;border:2px solid " + C.black + ";" +
      "background:" + SIMON_PAINT[colour][1] + ";color:" + C.black + ";font-size:18px;" +
      "display:flex;align-items:center;justify-content:center;", SIMON_SHAPE[colour]);
  }

  function renderSimon(body) {
    body.appendChild(el("div", "position:absolute;left:0;top:0;width:540px;font-size:14px;",
      "The pad to press is the one opposite the colour that flashed — never the " +
      "colour that flashed."));
    SIMON_COLOURS.forEach(function (colour, index) {
      var row = at(el("div"), 0, 40 + index * 54, 420, 46);
      row.style.cssText += "display:flex;align-items:center;gap:14px;font-size:20px;" +
        "font-weight:700;border:3px solid " + C.black + ";background:" + C.white + ";padding:0 12px;";
      row.appendChild(swatch(colour));
      row.appendChild(el("div", "width:96px;text-transform:uppercase;", colour));
      row.appendChild(el("div", "font-size:22px;", "→"));
      row.appendChild(swatch(SIMON_MAP[colour]));
      row.appendChild(el("div", "text-transform:uppercase;", SIMON_MAP[colour]));
      body.appendChild(row);
    });
    body.appendChild(el("div", "position:absolute;left:436px;top:44px;width:110px;font-size:12px;" +
      "line-height:1.5;",
      "Each stage replays from the start and adds one flash. There are no strikes: " +
      "the first wrong press is the last one."));
  }

  function renderNumber(body, options) {
    body.appendChild(el("div", "position:absolute;left:0;top:0;width:550px;font-size:13px;" +
      "line-height:1.45;",
      "The green 1 identifies the correct number grid — match it to the lit dot on " +
      "the bay. Find the displayed number in that grid; the answer is its " +
      (options.axis === "row" ? "row" : "column") +
      ": left = 1, middle = 2, right = 3."));
    NUMBER_PATTERNS.forEach(function (pattern, index) {
      var x = (index % 4) * 134 + 4;
      var y = Math.floor(index / 4) * 126 + 64;
      var box = el("div", "position:absolute;left:" + x + "px;top:" + y + "px;width:108px;" +
        "height:108px;border:3px solid " + C.black + ";background:" + C.white + ";");
      for (var r = 0; r < 3; r++) {
        for (var c = 0; c < 3; c++) {
          var value = pattern[r][c];
          box.appendChild(el("div", "position:absolute;left:" + (c * 34 + 1) + "px;top:" +
            (r * 34 + 1) + "px;width:32px;height:32px;display:flex;align-items:center;" +
            "justify-content:center;font-size:18px;font-weight:700;background:" +
            (value === 1 ? C.tip : C.white) + ";border:" +
            (value === 1 ? "3px solid " + C.black : "1px solid #bbb") + ";", value));
        }
      }
      body.appendChild(box);
    });
  }

  function renderMini(body) {
    body.appendChild(el("div", "position:absolute;left:0;top:0;width:540px;font-size:15px;" +
      "line-height:1.7;white-space:pre-line;",
      "Wait for the tiny button to turn red.\n" +
      "It must be pressed and held the moment it does.\n" +
      "Keep holding until it turns green.\n" +
      "The green button shows a two-digit code — that code is what proves the hold.\n" +
      "Release only after it has turned green."));
    body.appendChild(el("div", "position:absolute;left:0;top:170px;width:540px;font-size:13px;" +
      "line-height:1.5;font-weight:700;",
      "Pressing it before it turns red, reacting too slowly, or letting go early " +
      "all set the bomb off. Arming it is a commitment: the bay will not close " +
      "again until it is done."));
  }

  var RENDERERS = {
    maze: renderMaze, simon: renderSimon,
    according_to_number: renderNumber, mini_button: renderMini
  };

  /**
   * Draw the manual into `host`.
   *
   * options:
   *   page        "home" | one of PAGES
   *   onNavigate  fn(page)  — a selector entry was chosen
   *   onExit      fn()      — Exit was pressed on the home page
   *   axis        "column" | "row" — which axis the number bay reads (§62)
   *   homeNote    the line beside the selector; each seat says its own thing
   *   withheld    page ids this copy does not have (§2c). The Defuser's copy
   *               passes the board's `withheld_pages`; the Grandmaster's
   *               console passes nothing, which is what makes it the only
   *               copy of those pages in the match.
   */
  function render(host, options) {
    var page = options.page || "home";
    clear(host);
    var root = at(el("div"), 0, 0, W, H);
    root.style.cssText += "background:" + C.manualBg + ";color:" + C.black + ";" +
      "font-family:Arial,Helvetica,sans-serif;pointer-events:auto;";
    root.appendChild(el("div", "position:absolute;left:20px;top:14px;font-size:28px;" +
      "font-weight:700;", page === "home" ? "The Bomb:" : MODULE_NAMES[page]));

    // §28: a blue Exit in the upper-right, 53x34. From a page it walks back to
    // the home page; from home it hands control to the caller.
    var exit = button("Exit", "border:0;background:" + C.manualExitBlue + ";color:" +
      C.white + ";font-size:14px;font-weight:700;", function () {
        if (page === "home") options.onExit();
        else options.onNavigate("home");
      });
    at(exit, W - 73, 14, 53, 34);
    root.appendChild(exit);

    var body = at(el("div"), 20, 58, W - 40, H - 78);
    root.appendChild(body);
    if (page === "home") renderHome(body, options);
    else RENDERERS[page](body, options);

    if (page !== "home" && (options.withheld || []).indexOf(page) !== -1) {
      // Belt and braces: the selector offers no way in, but a caller holding a
      // stale page id must not be handed the one page it does not have.
      clear(body);
      body.appendChild(el("div", "position:absolute;left:0;top:0;width:540px;" +
        "font-size:15px;line-height:1.6;font-weight:700;",
        "This page is missing from your copy. Your Grandmaster has it — " +
        "describe the bay and they will read it to you."));
    }

    host.appendChild(root);
    return root;
  }

  window.RelayBombManual = {
    W: W, H: H, MAZE_SIZE: MAZE_SIZE, PAGES: PAGES, MODULE_NAMES: MODULE_NAMES,
    C: C, SIMON_PAINT: SIMON_PAINT, SIMON_SHAPE: SIMON_SHAPE,
    MAZE_LAYOUTS: MAZE_LAYOUTS, NUMBER_PATTERNS: NUMBER_PATTERNS,
    SIMON_COLOURS: SIMON_COLOURS, SIMON_MAP: SIMON_MAP,
    el: el, at: at, button: button, clear: clear,
    render: render,
    // Test hook: the static data both seats and the server's replay share.
    __data: {
      MAZE_LAYOUTS: MAZE_LAYOUTS, NUMBER_PATTERNS: NUMBER_PATTERNS,
      SIMON_MAP: SIMON_MAP, SIMON_COLOURS: SIMON_COLOURS
    }
  };
})();
