"""Ask the Hermes gateway about one session and optionally interrupt it. Prints {"running": bool}.

Runs inside Hermes' own runtime, started by delegate.py. Killing the `hermes chat` client only
detaches it: the session keeps running at the gateway until it is interrupted here.
"""
import asyncio
import json
import sys

from hermes_cli.gateway_client import GatewayClientError, connect_gateway


async def main(action, session_id):
    async with connect_gateway() as client:
        try:
            snapshot = await client.rpc("session.resume", session_id=session_id)
        except GatewayClientError as error:
            if str(error) != "not_found":
                raise
            return False
        running = bool(snapshot.get("running"))
        if running and action == "interrupt":
            await client.rpc("session.interrupt", session_id=snapshot["stored_session_id"],
                             execution_generation=snapshot["execution_generation"])
        return running


if __name__ == "__main__":
    print(json.dumps({"running": asyncio.run(main(sys.argv[1], sys.argv[2]))}))
