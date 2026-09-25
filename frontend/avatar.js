/* The Relay — avatar drawing, shared by the play client and the account page.
 *
 * A face is a short code (see `AVATAR_SLOTS` in backend/config.py): one
 * base-36 digit per feature. Players pick one at the join screen or on their
 * account page; a player who never picks is drawn from a code seeded off their
 * id instead, so every seat has a face and every client draws the same one
 * without a round trip.
 *
 * Security note, because this is the one place it bites: a code arrives over
 * the WebSocket from *other* players, and the drawing ends up in innerHTML. So
 * a code is never interpolated into markup. It is parsed to a list of
 * integers, each one taken modulo the length of the table it indexes, and the
 * SVG is assembled only from the literals in this file. A hostile code can
 * therefore pick an ugly face and nothing else.
 *
 * No build step here (see CLAUDE.md), so this is a plain script that hangs one
 * object off `window` and is loaded before the pages that use it.
 */
(function (global) {
  "use strict";

  function hashSeed(text) {
    var hash = 2166136261;
    for (var i = 0; i < text.length; i++) {
      hash ^= text.charCodeAt(i);
      hash = (hash * 16777619) >>> 0;
    }
    return hash;
  }

  // A tiny deterministic stream, so each feature of a face draws from its own
  // part of the seed instead of every avatar keying off the same low bits.
  function seedStream(seed) {
    var state = seed || 1;
    return function (n) {
      state ^= state << 13; state >>>= 0;
      state ^= state >> 17;
      state ^= state << 5; state >>>= 0;
      return state % n;
    };
  }

  var AVATAR_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz";

  // Slot order, and how many variants this client can draw for each. The
  // server holds the same numbers (config.AVATAR_SLOTS) and validates codes
  // against them; tests/test_avatars.py asserts the two never drift.
  var AVATAR_VARIANTS = {
    back: 8, skin: 8, eyes: 8, mouth: 8, brow: 5, hat: 8, extra: 7
  };
  var AVATAR_ORDER = ["back", "skin", "eyes", "mouth", "brow", "hat", "extra"];

  var INK = "#2b2233";
  var EYE_WHITE = "#fdfbff";
  var PINK = "#e2617a";
  var GOLD = "#ffd93d";

  // Ground and skin stay exactly as they were: the comedy lives in the hats and
  // the faces, not in the skin tones. A joke made out of somebody's colouring
  // is a different thing from a joke made out of a propeller beanie.
  var AVATAR_BACKS = ["#2b3a7a", "#1f5b6b", "#5b2f7a", "#7a2f4d", "#2f6b45",
                      "#6b5a1f", "#3a3a6b", "#6b3a2f"];
  var AVATAR_SKINS = ["#ffd9a8", "#f2b98c", "#d69a6a", "#a9713f", "#7a4f2b",
                      "#f7e2c8", "#c98c5a", "#8d5a34"];

  function rect(x, y, w, h, fill) {
    return '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h +
      '" fill="' + fill + '"/>';
  }

  // Each of these returns markup for one feature. They are pure and take only
  // numbers, so nothing a player controls can reach the string.
  var EYES = [
    function () { return rect(5, 7, 2, 2, INK) + rect(9, 7, 2, 2, INK); },
    // Googly: two sizes, two directions, deliberately not agreeing.
    function () {
      return rect(4, 6, 3, 3, EYE_WHITE) + rect(9, 6, 3, 3, EYE_WHITE) +
        rect(5, 8, 1, 1, INK) + rect(10, 6, 1, 1, INK);
    },
    function () {
      return rect(4, 6, 3, 3, EYE_WHITE) + rect(9, 6, 3, 3, EYE_WHITE) +
        rect(5, 7, 1, 1, INK) + rect(10, 7, 1, 1, INK);
    },
    function () { return rect(4, 8, 3, 1, INK) + rect(9, 8, 3, 1, INK); },
    function () { return rect(4, 8, 3, 1, INK) + rect(9, 7, 2, 2, INK); },
    // Cross-eyed: both pupils driven to the inside corners.
    function () {
      return rect(4, 6, 3, 3, EYE_WHITE) + rect(9, 6, 3, 3, EYE_WHITE) +
        rect(6, 7, 1, 1, INK) + rect(9, 7, 1, 1, INK);
    },
    function () { return rect(5, 8, 1, 1, INK) + rect(10, 8, 1, 1, INK); },
    // Half-lidded, for the permanently unimpressed.
    function () {
      return rect(4, 6, 3, 3, EYE_WHITE) + rect(9, 6, 3, 3, EYE_WHITE) +
        rect(4, 6, 3, 1, INK) + rect(9, 6, 3, 1, INK) +
        rect(5, 7, 1, 1, INK) + rect(10, 7, 1, 1, INK);
    }
  ];

  var MOUTHS = [
    function () { return rect(6, 11, 4, 1, INK); },
    function () { return rect(5, 10, 6, 2, INK) + rect(6, 10, 4, 1, EYE_WHITE); },
    function () { return rect(7, 10, 2, 2, INK); },
    function () {
      return rect(5, 11, 1, 1, INK) + rect(6, 10, 1, 1, INK) +
        rect(7, 11, 1, 1, INK) + rect(8, 10, 1, 1, INK) + rect(9, 11, 1, 1, INK);
    },
    function () { return rect(6, 10, 4, 1, INK) + rect(7, 11, 2, 2, PINK); },
    function () {
      return rect(5, 10, 6, 1, INK) + rect(6, 11, 1, 1, EYE_WHITE) +
        rect(9, 11, 1, 1, EYE_WHITE);
    },
    function () { return rect(5, 10, 6, 3, INK); },
    function () { return rect(6, 11, 3, 1, INK) + rect(9, 10, 1, 1, INK); }
  ];

  var BROWS = [
    function () { return ""; },
    function () { return rect(4, 5, 3, 1, INK) + rect(9, 5, 3, 1, INK); },
    function () {
      return rect(4, 4, 2, 1, INK) + rect(6, 5, 1, 1, INK) +
        rect(10, 4, 2, 1, INK) + rect(9, 5, 1, 1, INK);
    },
    function () { return rect(4, 4, 3, 1, INK) + rect(9, 4, 3, 1, INK); },
    function () { return rect(4, 5, 8, 1, INK); }
  ];

  var HATS = [
    function () { return ""; },
    // Tophat
    function () {
      return rect(2, 3, 12, 1, INK) + rect(4, 0, 8, 3, INK) +
        rect(4, 2, 8, 1, "#c0392b");
    },
    // Backwards cap
    function () { return rect(4, 1, 8, 3, "#3aa7a0") + rect(0, 3, 4, 1, "#3aa7a0"); },
    // Antenna with a bobble
    function () { return rect(8, 2, 1, 2, INK) + rect(7, 0, 3, 2, GOLD); },
    // Crown
    function () {
      return rect(4, 2, 8, 2, GOLD) + rect(4, 0, 2, 2, GOLD) +
        rect(7, 0, 2, 2, GOLD) + rect(10, 0, 2, 2, GOLD);
    },
    // Propeller beanie
    function () {
      return rect(5, 2, 6, 2, PINK) + rect(3, 1, 10, 1, "#3aa7a0") +
        rect(8, 0, 1, 1, INK);
    },
    // Party cone
    function () {
      return rect(7, 0, 2, 1, "#8e44ad") + rect(6, 1, 4, 1, "#8e44ad") +
        rect(5, 2, 6, 2, "#8e44ad") + rect(7, 0, 2, 1, GOLD);
    },
    // Little horns
    function () { return rect(3, 1, 2, 3, "#c0392b") + rect(11, 1, 2, 3, "#c0392b"); }
  ];

  var EXTRAS = [
    function () { return ""; },
    // Round spectacles. The frame is gold rather than ink on purpose: an ink
    // frame drawn over ink eyes merges into one solid bar and stops reading as
    // glasses at all. A different colour keeps the frame and the eye apart
    // whichever of the eight eyes is behind it.
    function () {
      return rect(4, 6, 3, 1, GOLD) + rect(4, 8, 3, 1, GOLD) +
        rect(4, 7, 1, 1, GOLD) + rect(6, 7, 1, 1, GOLD) +
        rect(9, 6, 3, 1, GOLD) + rect(9, 8, 3, 1, GOLD) +
        rect(9, 7, 1, 1, GOLD) + rect(11, 7, 1, 1, GOLD) +
        rect(7, 7, 2, 1, GOLD);
    },
    // Shades, which cover whatever the eyes were doing. That is the joke.
    function () {
      return rect(4, 6, 3, 2, INK) + rect(9, 6, 3, 2, INK) + rect(7, 6, 2, 1, INK);
    },
    // Moustache
    function () {
      return rect(5, 9, 6, 1, INK) + rect(4, 9, 1, 1, INK) + rect(11, 9, 1, 1, INK);
    },
    // Eyepatch
    function () { return rect(3, 6, 10, 1, INK) + rect(4, 6, 3, 3, INK); },
    // Blush
    function () { return rect(3, 9, 2, 1, PINK) + rect(11, 9, 2, 1, PINK); },
    // Clown nose
    function () { return rect(7, 9, 2, 1, PINK); }
  ];

  var AVATAR_TABLES = {
    back: AVATAR_BACKS, skin: AVATAR_SKINS, eyes: EYES,
    mouth: MOUTHS, brow: BROWS, hat: HATS, extra: EXTRAS
  };

  function avatarVariantCount(slot) { return AVATAR_TABLES[slot].length; }

  // A code as one index per slot, or null if it is not a code at all. Indices
  // are wrapped into range rather than trusted, so the draw below cannot read
  // past the end of a table however odd the input was.
  function avatarDecode(code) {
    if (typeof code !== "string" || code.length !== AVATAR_ORDER.length) return null;
    var out = [];
    for (var i = 0; i < AVATAR_ORDER.length; i++) {
      var at = AVATAR_DIGITS.indexOf(code.charAt(i).toLowerCase());
      if (at < 0) return null;
      out.push(at % avatarVariantCount(AVATAR_ORDER[i]));
    }
    return out;
  }

  function avatarEncode(parts) {
    return AVATAR_ORDER.map(function (slot, i) {
      return AVATAR_DIGITS.charAt(parts[i] % avatarVariantCount(slot));
    }).join("");
  }

  // The face a player gets when they never picked one: stable for the whole
  // match, identical on every client, and free of a network round trip.
  function avatarSeeded(seed) {
    var pick = seedStream(seed);
    return AVATAR_ORDER.map(function (slot) {
      return pick(avatarVariantCount(slot));
    });
  }

  function avatarRandom() {
    return AVATAR_ORDER.map(function (slot) {
      return Math.floor(Math.random() * avatarVariantCount(slot));
    });
  }

  function avatarSvg(parts) {
    var body = [
      rect(0, 0, 16, 16, AVATAR_BACKS[parts[0]]),
      rect(3, 4, 10, 10, AVATAR_SKINS[parts[1]]),
      BROWS[parts[4]](),
      EYES[parts[2]](),
      EXTRAS[parts[6]](),   // glasses and shades sit over the eyes
      MOUTHS[parts[3]](),
      HATS[parts[5]]()      // and a hat sits over everything
    ].join("");
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" ' +
      'shape-rendering="crispEdges" width="40" height="40">' + body + "</svg>";
  }

  // The parts to draw for a player: their pick when they made one, the face
  // seeded from their id when they did not.
  function avatarParts(state, player) {
    return avatarDecode(player.avatar) ||
      avatarSeeded(hashSeed(state.id + ":" + player.id));
  }

  // Spoken names for the features, so a face is describable to a screen reader
  // and the picker is not twelve identically-labelled buttons. Index order
  // matches the tables above.
  var EYE_WORDS = ["plain eyes", "googly eyes", "wide eyes", "squinting eyes",
                   "a wink", "crossed eyes", "beady eyes", "half-closed eyes"];
  var MOUTH_WORDS = ["a straight face", "a toothy grin", "a little O",
                     "a squiggly mouth", "a stuck-out tongue", "fangs",
                     "a wide gawp", "a smirk"];
  var BROW_WORDS = ["", "flat eyebrows", "angry eyebrows", "raised eyebrows",
                    "one big eyebrow"];
  var HAT_WORDS = ["", "a top hat", "a backwards cap", "an antenna", "a crown",
                   "a propeller beanie", "a party hat", "little horns"];
  var EXTRA_WORDS = ["", "spectacles", "sunglasses", "a moustache", "an eyepatch",
                     "rosy cheeks", "a clown nose"];

  function avatarLabel(parts) {
    var words = [EYE_WORDS[parts[2]], MOUTH_WORDS[parts[3]], BROW_WORDS[parts[4]],
                 HAT_WORDS[parts[5]], EXTRA_WORDS[parts[6]]].filter(Boolean);
    return words.join(", ");
  }
  global.RelayAvatar = {
    ORDER: AVATAR_ORDER,
    VARIANTS: AVATAR_VARIANTS,
    variantCount: avatarVariantCount,
    decode: avatarDecode,
    encode: avatarEncode,
    seeded: avatarSeeded,
    random: avatarRandom,
    svg: avatarSvg,
    label: avatarLabel,
    hashSeed: hashSeed
  };
})(window);
