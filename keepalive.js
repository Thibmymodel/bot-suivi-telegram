const express = require("express");
const app = express();
const PORT = process.env.PORT || 3000;

app.get("/", (req, res) => {
  res.send("✅ Bot is running – keep-alive OK");
});

app.listen(PORT, () => {
  console.log(`✅ Keep-alive server actif sur le port ${PORT}`);
});
