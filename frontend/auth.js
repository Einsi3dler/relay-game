/* The account pages. Vanilla, no build step, same as the rest of the client.
 *
 * One page serves /signup, /login, /forgot, /reset, /verify, /magic and
 * /account. The path picks the panel; everything else on the page is shared.
 *
 * The tokens in emailed links are NOT spent by opening the link. The page
 * loads, and then posts the token from here. Mail scanners at plenty of
 * companies follow every link in a message before the human sees it, and a
 * single-use token consumed on GET would already be gone by then.
 */
(function () {
  "use strict";

  var params = new URLSearchParams(window.location.search);
  var path = window.location.pathname.replace(/\/+$/, "") || "/account";
  var token = params.get("token") || "";

  // Where to go once signed in. Only ever a path on this site: taking a full
  // URL here would turn the sign-in page into an open redirect, which is how
  // a convincing phishing link gets to start at your own domain.
  var next = params.get("next") || "";
  if (!/^\/[^/\\]/.test(next)) next = "/play";

  function $(id) { return document.getElementById(id); }

  function show(viewId) {
    ["view-account", "view-login", "view-signup", "view-forgot", "view-reset",
     "view-token"].forEach(function (id) {
      var el = $(id);
      if (el) el.hidden = id !== viewId;
    });
  }

  // --- the one status line -------------------------------------------------

  function note(message, tone) {
    var el = $("auth-note");
    el.textContent = message || "";
    el.classList.remove("auth-note--bad", "auth-note--good");
    if (tone) el.classList.add("auth-note--" + tone);
    el.hidden = !message;
  }

  function clearNote() { note(""); }

  // --- fetch helpers -------------------------------------------------------

  // Every call is same-origin and the session rides in an HttpOnly cookie, so
  // "same-origin" credentials is both necessary and sufficient. Errors come
  // back as FastAPI's {detail: "..."} and are surfaced verbatim: the server
  // already phrases them for the person who typed the form.
  function api(url, options) {
    options = options || {};
    options.credentials = "same-origin";
    if (options.body !== undefined) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(options.body);
    }
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) {
          throw new Error(data.detail || "Something went wrong. Try again.");
        }
        return data;
      });
    });
  }

  function post(url, body) { return api(url, { method: "POST", body: body || {} }); }

  /* Submit handling, minus the four lines of bookkeeping every form repeats.
   * Disabling the button matters more than it looks: a signup posted twice
   * because someone double-clicked produces one account and one confusing
   * "that email is taken" error on the second attempt. */
  function onSubmit(formId, handler) {
    var form = $(formId);
    if (!form) return;
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var button = form.querySelector("button[type=submit]");
      var label = button ? button.textContent : "";
      if (button) { button.disabled = true; button.textContent = "Working..."; }
      clearNote();
      handler(new FormData(form)).catch(function (error) {
        note(error.message, "bad");
      }).then(function () {
        if (button) { button.disabled = false; button.textContent = label; }
      });
    });
  }

  function go(url) { window.location.href = url; }

  // --- panels --------------------------------------------------------------

  function renderAccount(user) {
    show("view-account");
    $("account-who").textContent =
      "Signed in as " + user.username + ".";
    $("account-facts").innerHTML = "";
    [["Name", user.first_name + " " + user.last_name],
     ["Username", user.username],
     ["Email", user.email + (user.verified ? " (confirmed)" : " (not confirmed yet)")]
    ].forEach(function (pair) {
      var dt = document.createElement("dt");
      var dd = document.createElement("dd");
      dt.textContent = pair[0];
      dd.textContent = pair[1];
      $("account-facts").appendChild(dt);
      $("account-facts").appendChild(dd);
    });
    $("verify-banner").hidden = !!user.verified;
  }

  function tokenResult(title, lede, tone) {
    $("token-title").textContent = title;
    $("token-lede").textContent = lede;
    $("token-actions").hidden = tone !== "bad";
  }

  /* /verify and /magic differ only in which endpoint they post to and what
   * they say afterwards, so they share everything else. */
  function spendToken(endpoint, workingText, doneTitle, doneLede) {
    show("view-token");
    if (!token) {
      tokenResult("That link is incomplete",
        "The address is missing its token. Open the link from your email again, or ask for a new one.",
        "bad");
      return;
    }
    tokenResult("One moment", workingText);
    post(endpoint, { token: token }).then(function () {
      tokenResult(doneTitle, doneLede);
      setTimeout(function () { go(next); }, 1200);
    }).catch(function (error) {
      tokenResult("That link did not work", error.message, "bad");
    });
  }

  function startReset() {
    show("view-reset");
    if (!token) {
      show("view-token");
      tokenResult("That link is incomplete",
        "The address is missing its token. Open the link from your email again, or ask for a new one.",
        "bad");
      return;
    }
    // Check before drawing the form. Letting someone pick and confirm a new
    // password against a link that expired an hour ago, only to be told after
    // they submit, is a needlessly rude way to spend their time.
    api("/api/auth/reset/check?token=" + encodeURIComponent(token))
      .then(function (data) {
        if (!data.valid) {
          show("view-token");
          tokenResult("That reset link has expired",
            "Reset links last an hour and can be used once. Ask for a new one and it will be waiting in your inbox.",
            "bad");
          return;
        }
        $("reset-who").textContent = "Setting a new password for " + data.email + ".";
      })
      .catch(function () {
        show("view-token");
        tokenResult("That reset link did not work",
          "Ask for a new one and try again.", "bad");
      });
  }

  // --- wiring --------------------------------------------------------------

  onSubmit("form-login", function (data) {
    return post("/api/auth/login", {
      email: data.get("email"),
      password: data.get("password")
    }).then(function () { go(next); });
  });

  onSubmit("form-signup", function (data) {
    return post("/api/auth/signup", {
      first_name: data.get("first_name"),
      last_name: data.get("last_name"),
      email: data.get("email"),
      username: data.get("username"),
      password: data.get("password"),
      confirm_password: data.get("confirm_password")
    }).then(function () { go(next); });
  });

  onSubmit("form-forgot", function (data) {
    return post("/api/auth/forgot", {
      email: data.get("email"),
      mode: data.get("mode") || "reset"
    }).then(function (result) {
      $("form-forgot").reset();
      note(result.message, "good");
    });
  });

  onSubmit("form-reset", function (data) {
    return post("/api/auth/reset", {
      token: token,
      password: data.get("password"),
      confirm_password: data.get("confirm_password")
    }).then(function () { go(next); });
  });

  // "Email me a sign-in link" from the sign-in page. It carries the address
  // already typed above, so the usual answer is one click.
  var wantMagic = $("want-magic");
  if (wantMagic) {
    wantMagic.addEventListener("click", function () {
      var email = ($("login-email").value || "").trim();
      if (!email) {
        note("Type your email address above first, then ask for the link.", "bad");
        $("login-email").focus();
        return;
      }
      wantMagic.disabled = true;
      post("/api/auth/forgot", { email: email, mode: "magic" })
        .then(function (result) { note(result.message, "good"); })
        .catch(function (error) { note(error.message, "bad"); })
        .then(function () { wantMagic.disabled = false; });
    });
  }

  var resend = $("resend-verify");
  if (resend) {
    resend.addEventListener("click", function () {
      resend.disabled = true;
      post("/api/auth/verify/resend")
        .then(function (result) { note(result.message, "good"); })
        .catch(function (error) { note(error.message, "bad"); })
        .then(function () { resend.disabled = false; });
    });
  }

  var logout = $("logout");
  if (logout) {
    logout.addEventListener("click", function () {
      post("/api/auth/logout").then(function () { go("/login"); })
        .catch(function () { go("/login"); });
    });
  }

  // --- route ---------------------------------------------------------------

  if (path === "/verify") {
    spendToken("/api/auth/verify", "Confirming your address.",
      "Email confirmed", "Thanks. Taking you to the game.");
    return;
  }

  if (path === "/magic") {
    spendToken("/api/auth/magic", "Signing you in.",
      "You are in", "Taking you to the game.");
    return;
  }

  if (path === "/reset") {
    startReset();
    return;
  }

  if (path === "/forgot") {
    show("view-forgot");
    return;
  }

  /* /login, /signup and /account all depend on whether there is a session, so
   * they wait for the answer. An already-signed-in person landing on /login
   * wants the account card, not a form asking them to do it again. */
  api("/api/auth/me").then(function (data) {
    if (data.user) {
      if (path === "/signup" || path === "/login") { go(next); return; }
      renderAccount(data.user);
      return;
    }
    if (path === "/signup") { show("view-signup"); return; }
    if (path === "/account") {
      show("view-login");
      note("Sign in to see your account.", null);
      return;
    }
    show("view-login");
  }).catch(function () {
    // The account API is unreachable. Showing the sign-in form is the only
    // useful thing left, and guest play does not need any of this anyway.
    show(path === "/signup" ? "view-signup" : "view-login");
    note("Could not reach the server. You can still play as a guest.", "bad");
  });
})();
