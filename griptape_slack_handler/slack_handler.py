import os
import logging
from slack_bolt import App, Say, BoltRequest
from slack_sdk import WebClient

from .slack_util import (
    error_payload,
    thinking_payload,
    markdown_blocks_list,
)
from .griptape_handler import agent, get_rulesets, try_add_to_thread
from .griptape_event_handlers import event_listeners

logger = logging.getLogger()

app: App = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
    logger=logger,
    process_before_response=True,  # required
)

### Slack Event Handlers ###


@app.event("message")
def message(body: dict, payload: dict, say: Say, client: WebClient):
    # filter out messages from the bot itself to prevent infinite loops
    bot_user_id = client.auth_test()["user_id"]
    if payload.get("user") == bot_user_id:
        return

    # filter for messages that are not new user signups.
    # add these to thread to populate conversation history
    # to allow for thread conversations.
    if "New signup by" not in payload.get("text"):
        try_add_to_thread(
            payload["text"],
            thread_alias=payload.get("thread_ts", payload["ts"]),
            user_id=payload["user"],
        )

        return

    respond_in_thread(body, payload, say, client)


@app.event("app_mention")
def app_mention(body: dict, payload: dict, say: Say, client: WebClient):
    respond_in_thread(body, payload, say, client)


def respond_in_thread(body: dict, payload: dict, say: Say, client: WebClient):
    team_id = body["team_id"]
    app_id = body["api_app_id"]
    thread_ts = payload.get("thread_ts", payload["ts"])
    ts = say(
        **thinking_payload(),
        thread_ts=thread_ts,
    )["ts"]

    stream = False

    try:
        rulesets = get_rulesets(
            user_id=payload["user"],
            channel_id=payload["channel"],
            team_id=team_id,
            app_id=app_id,
        )
        # wip, if any rulesets have stream=True, then stream the response
        # changes the slack app behavior. any truthy value will work
        stream = any([ruleset.meta.get("stream", False) for ruleset in rulesets])
        # wip, if any rulesets have enable_toolbox=True, then enable dynamic
        # tool selection for the agent
        enable_toolbox = any(
            [ruleset.meta.get("enable_toolbox", False) for ruleset in rulesets]
        )

        agent_output = agent(
            payload["text"],
            thread_alias=thread_ts,
            user_id=payload["user"],
            rulesets=rulesets,
            event_listeners=event_listeners(
                stream=stream,
                web_client=client,
                ts=ts,
                thread_ts=thread_ts,
                channel=payload["channel"],
            ),
            stream=stream,
            enable_toolbox=enable_toolbox,
        )
    except Exception as e:
        logger.exception("Error while processing response")
        client.chat_postMessage(
            **error_payload(str(e)),
            ts=ts,
            thread_ts=thread_ts,
            channel=payload["channel"],
            channel_type=payload.get("channel_type"),
        )
        return

    # Assuming that the response is already sent if its being streamed
    if not stream:
        for i, blocks in enumerate(markdown_blocks_list(agent_output)):
            if i == -1:
                client.chat_update(
                    text=agent_output,
                    blocks=blocks,
                    ts=ts,
                    channel=payload["channel"],
                )
            else:
                client.chat_postMessage(
                    text=agent_output,
                    blocks=blocks,
                    thread_ts=thread_ts,
                    channel=payload["channel"],
                )


def handle_slack_event(body: str, headers: dict) -> dict:
    req = BoltRequest(body=body, headers=headers)
    bolt_response = app.dispatch(req=req)
    return {
        "status": bolt_response.status,
        "body": bolt_response.body,
        "headers": bolt_response.headers,
    }
