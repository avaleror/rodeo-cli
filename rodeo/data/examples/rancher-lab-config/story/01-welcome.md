# Welcome to the rodeo

<span lang="en" id="welcome.intro">Saddle up. In this lab you run your own
Rancher Prime on K3s and take it for a ride.</span>

<span lang="en" id="welcome.login">Open <span no id="welcome.rancher-url">{{ rancher_url }}</span>
in a browser (accept the self-signed certificate) and log in as
<span no id="welcome.user">admin</span>.</span>

<span lang="en" id="welcome.first-task" hist="first-ride">Your first ride:
explore Cluster Management, then install an app from Charts.</span>

<!--
rodeo story authoring notes (delete in your own labs):
- Text inside <span lang="en" id="..."> is translatable; ids key the
  translation store in story/strings/ (see `rmstory extract`).
- <span no> content is invariant: never translated. Put Jinja deployment
  facts there ({{ rancher_url }}, {{ vip }}, {{ credentials }}, ...) so
  machine translation can't corrupt them.
- hist="<story-id>" assigns a span to a story variant; the ordered index
  lives in story/stories/<story-id>.yaml.
- Render:  rodeo story render [--language es] [--story-id first-ride]
-->
