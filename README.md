# mongosemantic

Zero-config semantic search for any MongoDB database.

```bash
pip install mongosemantic
mongosemantic inspect --collection articles
mongosemantic apply --collection articles --field body
mongosemantic index --collection articles
mongosemantic search "budget travel"
```

Works on MongoDB Atlas, self-hosted replica sets, and standalone MongoDB 7.0+.

Full docs coming in v0.2.0.
