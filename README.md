# Websockets exposer for the spacenav driver

This is a Python cli to make Onshape work with [FreeSpacenav/spacenavd](https://github.com/FreeSpacenav/spacenavd) on Linux. Installing spacenavd is a prerequisite for this program to work but otherwise it is self contained. Pull requests and ideas for improvement are very welcome! As is any and all good documentation on what 3dconnexion and Onshape are doing under the hood. This is basically reverse engineered from looking at some websocket traffic and I'm sure with more knowledge much can be improved!

# Running it

You must have the spacenavdriver installed and running and then simply running: `uvx spacenav-ws` should get you up and running! Sadly there are several more snags to _really_ get underway.
 1) The first is to check if the spacemousedriver is up and running and if you can connect to it: Run `uvx spacenav-ws@latest read-mouse` to check that!
 2) The second is that this server must run with tls but obviously no-one will issue a cert for "127.51.68.120". Because of that I've self issued a cert which your browser will not trust by default. Start the server using: `uvx spacenav-ws@latest serve` and navigate to `https://127.51.68.120:8181`. Your browser should prompt you to add an exception and so trust the cert! Do so and the same mouse motions should appear on screen in your browser.
 3) Onshape does currently not even look for the mouse websocket on Linux. To do this you must trick Onshape into thinking you are on a Windows system. For this Onshape helpfully check [this deprecated property](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/platform). I have not found any slick way to set this flag using a simple bookmark or something so you must install an extension like [Tampermonkey](https://addons.mozilla.org/en-US/firefox/addon/tampermonkey/?utm_source=addons.mozilla.org&utm_medium=referral&utm_content=search) or something similar in your browser.. Something that allows executing JS before a pageload properly completes. If you install Tampermonkey this link should be a oneclick install to get up and running! https://greasyfork.org/en/scripts/533516-onshape-3d-mouse-on-linux-in-page-patch I hope to convince Onshape to start checking for the websocket on Linux now that this project is working well and thus remove this step..

# Developing
Download the repo and run: `uv run spacenav-ws serve --hot-reload` this starts the server with Uvicorn's `code watcing / hot reload` feature enabled.

# Deploying to pypi
Just run

```bash
uv build
uv publish
```
