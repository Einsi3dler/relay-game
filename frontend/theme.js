// The Relay — one palette for every board.
//
// Every game used to bake its own hex values into inline styles, which meant
// the same colour lived in five files under five names, and nothing could be
// retuned without a sweep. Boards read from here instead, the way the engine's
// numbers all come from backend/config.py.
//
// Two rules for anything added here:
//
//   * A name says what the colour *means*, never what it looks like. `goal` and
//     `hazard` survive a redesign; `orange` and `red` do not.
//   * Colour is never the only carrier of meaning. Every board that uses
//     `swatch` pairs it with a shape, a letter or a number, so the boards stay
//     playable without seeing hue. Do not remove that pairing to lean on a
//     colour added here.
//
// Loaded before the game renderers in index.html and explore.html, so it is on
// `window` by the time any of them mounts.
(function () {
  "use strict";

  var T = {
    // --- the ground ------------------------------------------------------
    bgDeep: "#07091f",   // the well a board sits in
    bg: "#0c1230",       // the board itself
    cell: "#141d47",     // one playable cell
    cellAlt: "#101838",  // its alternate, where a board stripes
    grid: "#243363",     // grid lines and cell borders
    line: "rgba(122, 140, 255, 0.25)",
    wall: "#3b82ff",     // a wall you cannot pass, lit

    // --- ink -------------------------------------------------------------
    text: "#f7f8ff",
    muted: "#9aa5cb",
    ink: "#07091f",      // text sitting on a bright fill

    // --- meaning ---------------------------------------------------------
    accent: "#8a4dff",   // selection, the active thing, the badge
    good: "#42db83",     // solved, correct, done
    hazard: "#ff5165",   // loses the run
    warn: "#ffa62b",
    goal: "#ffd21c",     // where you are trying to get to
    source: "#ffd21c",   // where the power/flow starts
    sink: "#ff6b9d",     // where it has to arrive

    // --- the two sides of a paired board ---------------------------------
    // Mirror Run's two runners, and anything else that shows the same board
    // twice under different rules.
    sideA: "#20d4e7",
    sideB: "#ff4f94",

    // --- pieces ----------------------------------------------------------
    // One categorical set, so a Decant tube, an Echo pad, an Overprint layer
    // and a Lane Shift packet all read as pieces of the same game. Ordered so
    // that any two adjacent entries stay distinguishable without hue.
    swatch: [
      "#3b82ff",  // blue
      "#ff5f6d",  // red
      "#ffc93c",  // yellow
      "#3ddc84",  // green
      "#c77dff",  // violet
      "#20d4e7"   // cyan
    ],

    // --- surfaces of a solid ---------------------------------------------
    // Shadow Cast draws a 3D block: three faces of one material, lit from one
    // direction, so they have to stay a family rather than three colours.
    faceTop: "#7ce8ff",
    faceFront: "#2f9fd8",
    faceSide: "#1c5f96"
  };

  // The neon look is a glow, and a glow is a box-shadow. Kept here so the
  // strength is consistent across boards instead of guessed per file.
  T.glow = function (colour, strength) {
    var px = strength || 8;
    return "0 0 " + px + "px " + T.fade(colour, 0.55);
  };

  // A hex at partial alpha, for borders and washes over the board ground.
  // Takes the 6-digit hexes above; anything else is handed back untouched so a
  // caller passing an rgba() string still gets something valid.
  T.fade = function (colour, alpha) {
    if (typeof colour !== "string" || colour.charAt(0) !== "#" ||
        colour.length !== 7) {
      return colour;
    }
    var r = parseInt(colour.slice(1, 3), 16);
    var g = parseInt(colour.slice(3, 5), 16);
    var b = parseInt(colour.slice(5, 7), 16);
    return "rgba(" + r + ", " + g + ", " + b + ", " + alpha + ")";
  };

  // Nth piece colour, wrapping. Every caller was writing this modulo itself.
  T.piece = function (index) {
    return T.swatch[((index % T.swatch.length) + T.swatch.length) %
      T.swatch.length];
  };

  window.RelayTheme = T;
})();
