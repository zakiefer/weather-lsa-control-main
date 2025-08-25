import express from "express";

const app = express();

app.get("/api/oauth/callback", (req, res) => {
  try {
    const qs = new URLSearchParams(req.query).toString();
    const target = `http://127.0.0.1:3845/api/oauth/callback${qs ? `?${qs}` : ""}`;
    res.redirect(target);
  } catch (_e) {
    res.status(500).send("Proxy error");
  }
});

app.listen(3000, () => {
  console.log("Figma OAuth proxy on http://localhost:3000/api/oauth/callback");
});
