/* Game Night — the bits every general-site page shares.
 *
 * One job for now: the account chip in the top bar. Signed in, it says who
 * you are and opens your account; signed out, it is a "Sign in" link that
 * brings you back to this page afterwards. Asks /api/auth/me, which never
 * 401s, so a signed-out visitor costs one cheap request and nothing else.
 *
 * No build step (see CLAUDE.md).
 */
(function () {
  "use strict";

  var chip = document.getElementById("site-account");
  if (!chip) return;

  function signedOut() {
    var here = location.pathname + location.search;
    chip.href = "/login" + (here && here !== "/login" ? "?next=" + encodeURIComponent(here) : "");
    chip.textContent = "Sign in";
    chip.hidden = false;
  }

  fetch("/api/auth/me", { credentials: "same-origin" })
    .then(function (res) { return res.ok ? res.json() : {}; })
    .then(function (data) {
      if (!data.user) { signedOut(); return; }
      chip.href = "/account";
      chip.textContent = "Hi, " + (data.user.first_name || data.user.username);
      chip.hidden = false;
    })
    .catch(signedOut);
})();
