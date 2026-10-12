#!/usr/bin/env python3
"""Staged deploy for Goal Tracker: PREPROD first, then PROD.

WHY THIS EXISTS
  Before: every app edit was committed to index.html AND beta/ AND preview/ in
  ONE commit -> /preview/ was a mirror, never a gate. Prod and preprod always
  moved together, so a broken build reached everyone instantly.

NOW (canary / staged promotion):
  stage   <app.html>  -> writes ONLY preview/index.html  (preprod, owner-only)
  ...owner verifies on /preview/?cohort=preview...
  promote             -> copies the VERIFIED preview body to index.html (prod)
                         and beta/index.html. Root/beta only change on promote.

THE THREE SERVED FILES ARE IDENTICAL EXCEPT ONE <head> GATE:
  * index.html  = ROOT_GATE    + BODY   (fail-open router to beta/preview)
  * beta/...    = BETA_GATE    + BODY   (return guard, cohort=beta)
  * preview/... = PREVIEW_GATE + BODY   (return guard, cohort=preview, owner-exempt)
The gate is INFRASTRUCTURE, the BODY is the app. Staging/promoting only ever
moves the BODY; the gate is re-injected from the constants below. That is what
makes "edit once, promote deliberately" possible and safe.

USAGE (gh token is a required argv, never hardcoded):
  python3 deploy_staged.py <gh_token> status
  python3 deploy_staged.py <gh_token> stage   <local_app.html> [--dry-run]
  python3 deploy_staged.py <gh_token> promote                  [--dry-run]

Exit codes: 0 ok, 1 error, 2 refused (e.g. nothing to promote).
"""
import sys, re, os, json, base64, subprocess, tempfile, urllib.request, urllib.error

REPO = "korobovaelena00-cmd/goal-tracker"
API = "https://api.github.com"
APP_PREFIX = '<script src="https://telegram.org/js/telegram-web-app.js"></script>'
APP_MARKER = "Пупс-ай"                      # present in every real build
BACKEND = "https://goal-tracker-api.korobovaelena.workers.dev"

# --- Gates: infrastructure, embedded SSOT. (status verifies live vs embedded) ---
ROOT_GATE = """<script>
/* --- Level 1 cohort router (fail-open: any error -> stay stable) --- */
(function(){
  var BACKEND="%s";
  function id(){try{return (window.Telegram&&window.Telegram.WebApp&&window.Telegram.WebApp.initData)||"";}catch(e){return "";}}
  function go(){
    var q=location.search||"";
    var m=q.match(/[?&]cohort=(beta|preview)/);
    if(m){location.replace("./"+m[1]+"/"+q);return;}
    var t=id();
    if(!t){return;}
    try{
      var x=new XMLHttpRequest(); x.timeout=6000;
      x.open("GET",BACKEND+"/cohort/me",true);
      x.setRequestHeader("X-Init-Data",t);
      x.onload=function(){
        var c="stable";
        try{if(x.status===200){var j=JSON.parse(x.responseText);if(j&&j.cohort)c=j.cohort;}}catch(e){return;}
        if(c==="beta"||c==="preview"){location.replace("./"+c+"/"+(location.search||""));}
      };
      x.send();
    }catch(e){}
  }
  if(document.readyState==="loading"){document.addEventListener("DOMContentLoaded",go);}else{go();}
})();
</script>""" % BACKEND

BETA_GATE = """<script>
/* --- Level 1 cohort guard: bounce to stable if cohort changed --- */
(function(){
  var BACKEND="%s", MINE="beta";
  function id(){try{return (window.Telegram&&window.Telegram.WebApp&&window.Telegram.WebApp.initData)||"";}catch(e){return "";}}
  try{
    var q=location.search||"";
    var m=q.match(/[?&]cohort=(beta|preview)/);
    if(m&&m[1]===MINE){return;}
    var t=id();
    if(t){
      var x=new XMLHttpRequest(); x.timeout=6000;
      x.open("GET",BACKEND+"/cohort/me",true);
      x.setRequestHeader("X-Init-Data",t);
      x.onload=function(){
        var c="stable";
        try{if(x.status===200){var j=JSON.parse(x.responseText);if(j&&j.cohort)c=j.cohort;}}catch(e){return;}
        if(c!==MINE){location.replace("../");}
      };
      x.send();
    }
  }catch(e){}
})();
</script>""" % BACKEND

PREVIEW_GATE = """<script>
/* --- Level 1 cohort guard: bounce to stable if cohort changed --- */
(function(){
  var BACKEND="%s", MINE="preview";
  function id(){try{return (window.Telegram&&window.Telegram.WebApp&&window.Telegram.WebApp.initData)||"";}catch(e){return "";}}
  try{
    var q=location.search||"";
    var m=q.match(/[?&]cohort=(beta|preview)/);
    if(m&&m[1]===MINE){return;}
    var t=id();
    if(t){
      var x=new XMLHttpRequest(); x.timeout=6000;
      x.open("GET",BACKEND+"/cohort/me",true);
      x.setRequestHeader("X-Init-Data",t);
      x.onload=function(){
        var c="stable";
        try{if(x.status===200){var j=JSON.parse(x.responseText);if(j&&j.cohort)c=j.cohort;}}catch(e){return;}
        var ow="";try{ow=String((JSON.parse(new URLSearchParams(t).get("user")||"{}").id)||"");}catch(e){}
        if(c!==MINE && ow!=="815804273"){location.replace("../");}
      };
      x.send();
    }
  }catch(e){}
})();
</script>""" % BACKEND

GATE_RE = re.compile(re.escape(APP_PREFIX) + r"<script>.*?</script>", re.DOTALL)


# ----------------------------- GitHub helpers -----------------------------
def gh(token, path, method="GET", data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(API + path, data=body, method=method, headers={
        "Authorization": "token " + token, "User-Agent": "Mozilla/5.0",
        "Accept": "application/vnd.github+json"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def head_sha(token):
    return gh(token, "/repos/%s/git/ref/heads/main" % REPO)["object"]["sha"]


def read_at(token, path, sha):
    """Exact content at a commit (blob-addressed, cache-proof)."""
    tree = gh(token, "/repos/%s/git/trees/%s?recursive=1" % (REPO, sha))
    entry = next((e for e in tree["tree"] if e["path"] == path and e["type"] == "blob"), None)
    if not entry:
        return None
    blob = gh(token, "/repos/%s/git/blobs/%s" % (REPO, entry["sha"]))
    return base64.b64decode(blob["content"]).decode("utf-8")


def commit_paths(token, parent, updates, message):
    """updates: {path: text}. One commit; paths not listed are untouched."""
    base_tree = gh(token, "/repos/%s/git/commits/%s" % (REPO, parent))["tree"]["sha"]
    tree = []
    for p, text in updates.items():
        blob = gh(token, "/repos/%s/git/blobs" % REPO, "POST", {"content": text, "encoding": "utf-8"})
        tree.append({"path": p, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    nt = gh(token, "/repos/%s/git/trees" % REPO, "POST", {"base_tree": base_tree, "tree": tree})
    c = gh(token, "/repos/%s/git/commits" % REPO, "POST",
           {"message": message, "tree": nt["sha"], "parents": [parent]})
    gh(token, "/repos/%s/git/refs/heads/main" % REPO, "PATCH", {"sha": c["sha"]})
    return c["sha"]


# ----------------------------- body/gate ops -----------------------------
def strip_gate(html):
    """Remove the head gate (if any). Returns (body, had_gate)."""
    return (GATE_RE.sub(APP_PREFIX, html, count=1), bool(GATE_RE.search(html)))


def install_gate(body, gate):
    assert APP_PREFIX in body, "app prefix anchor missing in body"
    return body.replace(APP_PREFIX, APP_PREFIX + gate, 1)


def js_check(html):
    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.DOTALL)
    js = "\n".join(s for s in scripts if "src=" not in s[:60])
    tf = tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False)
    tf.write(js); tf.close()
    try:
        r = subprocess.run(["node", "--check", tf.name], capture_output=True, text=True)
    except FileNotFoundError:
        print("  (node not found - JS syntax check skipped)")
        return
    finally:
        os.unlink(tf.name)
    if r.returncode != 0:
        raise SystemExit("JS SYNTAX ERROR:\n" + r.stderr[:600])


# ----------------------------- commands -----------------------------
def cmd_status(token):
    sha = head_sha(token)
    print("main HEAD:", sha[:12])
    bodies = {}
    for p in ("index.html", "beta/index.html", "preview/index.html"):
        t = read_at(token, p, sha)
        if t is None:
            print(" ", p, "MISSING"); continue
        body, had = strip_gate(t)
        bodies[p] = body
        gate_live = "ROOT" if p == "index.html" else ("BETA" if p.startswith("beta") else "PREVIEW")
        gate_emb = {"index.html": ROOT_GATE, "beta/index.html": BETA_GATE,
                    "preview/index.html": PREVIEW_GATE}[p]
        gate_ok = gate_live and (t.replace(APP_PREFIX, APP_PREFIX, 1) and gate_emb and had)
        print(" ", p, "len", len(t), "body_len", len(body), "gate_present", had,
              "gate_matches_embedded", bool(re.search(re.escape(gate_emb), t)))
    if len(bodies) == 3:
        same = bodies["index.html"] == bodies["beta/index.html"] == bodies["preview/index.html"]
        print("\nbodies identical across root/beta/preview:", same)
        print("promotable (preview body != root body):",
              bodies["preview/index.html"] != bodies["index.html"])
    return 0


def cmd_stage(token, src, dry):
    html = open(src, encoding="utf-8").read()
    if APP_MARKER not in html:
        raise SystemExit("REFUSED: %r not found in %s (not a real app build?)" % (APP_MARKER, src))
    body, had = strip_gate(html)
    out = install_gate(body, PREVIEW_GATE)
    js_check(out)
    print("stage: source %s (%d chars, had_gate=%s) -> preview/index.html (%d chars)"
          % (os.path.basename(src), len(html), had, len(out)))
    if dry:
        print("  --dry-run: not pushed"); return 0
    parent = head_sha(token)
    new = commit_paths(token, parent, {"preview/index.html": out},
                       "stage(preprod): %s -> preview only" % os.path.basename(src))
    print("  STAGED to preprod:", new[:12], "(prod index/beta untouched)")
    print("  verify at /goal-tracker/preview/?cohort=preview, then run: promote")
    return 0


def cmd_promote(token, dry):
    parent = head_sha(token)
    prev = read_at(token, "preview/index.html", parent)
    root = read_at(token, "index.html", parent)
    if prev is None or root is None:
        raise SystemExit("REFUSED: preview or index missing")
    body, _ = strip_gate(prev)
    if APP_MARKER not in body:
        raise SystemExit("REFUSED: staged preview body looks broken")
    if body == strip_gate(root)[0] and not dry:
        print("NOTE: preview body already equals prod body (re-promoting)")
    new_index = install_gate(body, ROOT_GATE)
    new_beta = install_gate(body, BETA_GATE)
    js_check(new_index)
    print("promote: preview body (%d chars) -> index.html (prod) + beta/index.html" % len(body))
    if dry:
        print("  --dry-run: not pushed"); return 0
    new = commit_paths(token, parent, {"index.html": new_index, "beta/index.html": new_beta},
                       "promote: verified preview -> prod (index + beta)")
    print("  PROMOTED to prod:", new[:12])
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__); return 1
    token = sys.argv[1]
    args = [a for a in sys.argv[2:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    if not args:
        print(__doc__); return 1
    cmd = args[0]
    if cmd == "status":
        return cmd_status(token)
    if cmd == "stage":
        if len(args) < 2:
            raise SystemExit("usage: stage <local_app.html>")
        return cmd_stage(token, args[1], dry)
    if cmd == "promote":
        return cmd_promote(token, dry)
    raise SystemExit("unknown command: %s" % cmd)


if __name__ == "__main__":
    sys.exit(main())
