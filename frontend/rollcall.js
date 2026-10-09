/* ROLL CALL — a participant's personal link.
 *
 * Two jobs behind one URL, because the invitation promised one link: pick the
 * name and face the room will see, and then get into the room. Somebody who
 * has already registered is sent straight through; the form is only in the
 * way the first time.
 *
 * It captures two things and no more. The three answers came from the form
 * that produced the roster and are shown here read-only, so the person can
 * see exactly what the room will be shown without being tempted to polish it
 * once they realise the game is real.
 *
 * The token is in the path, which makes it the credential: whoever holds the
 * link is that person. Right for a party game among colleagues, wrong for
 * anything else, and it is not authentication.
 */
(function (global) {
  "use strict";

  var token = decodeURIComponent(
    global.location.pathname.replace(/^\/rollcall\//, "").replace(/\/$/, ""));

  var el = {};
  ["stage-loading", "stage-bad", "stage-form", "stage-done", "register-form",
   "field-name", "field-face", "shuffle-face", "form-error", "my-answers",
   "done-face", "done-name", "to-room", "edit-again", "submit-btn"
  ].forEach(function (id) { el[id] = document.getElementById(id); });

  var STAGES = ["stage-loading", "stage-bad", "stage-form", "stage-done"];
  function show(which) {
    STAGES.forEach(function (id) { el[id].hidden = id !== which; });
  }

  var faceCode = null;
  var me = null;

  function drawFace() {
    var parts = faceCode ? global.RelayAvatar.decode(faceCode) : null;
    if (!parts) parts = global.RelayAvatar.seeded(global.RelayAvatar.hashSeed(token));
    el["field-face"].innerHTML = global.RelayAvatar.svg(parts);
  }

  function faceFor(person) {
    var parts = person.avatar ? global.RelayAvatar.decode(person.avatar) : null;
    if (!parts) parts = global.RelayAvatar.seeded(global.RelayAvatar.hashSeed(person.id));
    return global.RelayAvatar.svg(parts);
  }

  function renderAnswers(answers) {
    var list = el["my-answers"];
    while (list.firstChild) list.removeChild(list.firstChild);
    answers.forEach(function (entry) {
      var li = document.createElement("li");
      li.className = "answer";

      var label = document.createElement("span");
      label.className = "answer__label";
      label.textContent = entry.label;
      li.appendChild(label);

      var body = document.createElement("p");
      body.className = "answer__body";
      // textContent, never innerHTML: this is text somebody else typed.
      body.textContent = entry.body;
      li.appendChild(body);

      list.appendChild(li);
    });
  }

  function showForm() {
    el["field-name"].value = me.name || me.suggested_name || "";
    faceCode = me.avatar || global.RelayAvatar.encode(global.RelayAvatar.random());
    drawFace();
    renderAnswers(me.answers);
    show("stage-form");
    el["field-name"].focus();
  }

  function showDone() {
    el["done-face"].innerHTML = faceFor(me);
    el["done-name"].textContent = me.name;
    el["to-room"].href = "/quiz?token=" + encodeURIComponent(token);
    show("stage-done");
  }

  el["shuffle-face"].addEventListener("click", function () {
    faceCode = global.RelayAvatar.encode(global.RelayAvatar.random());
    drawFace();
  });

  el["edit-again"].addEventListener("click", showForm);

  el["register-form"].addEventListener("submit", function (event) {
    event.preventDefault();
    el["form-error"].textContent = "";
    el["submit-btn"].disabled = true;

    fetch("/api/rollcall/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: token,
        name: el["field-name"].value,
        avatar: faceCode
      })
    }).then(function (response) {
      return response.json().then(function (data) {
        return { ok: response.ok, data: data };
      });
    }).then(function (result) {
      el["submit-btn"].disabled = false;
      if (!result.ok) {
        el["form-error"].textContent = result.data.detail || "That did not work.";
        return;
      }
      me = result.data;
      showDone();
    }).catch(function () {
      el["submit-btn"].disabled = false;
      el["form-error"].textContent = "Could not reach the server. Try again.";
    });
  });

  fetch("/api/rollcall/me?token=" + encodeURIComponent(token))
    .then(function (response) {
      if (!response.ok) throw new Error("bad token");
      return response.json();
    })
    .then(function (data) {
      me = data;
      /* Already registered: the form has nothing to ask, so do not make them
         fill it in again to get to the room they were invited to. */
      if (me.registered) showDone();
      else showForm();
    })
    .catch(function () { show("stage-bad"); });
})(window);
