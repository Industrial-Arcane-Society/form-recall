import { createInterface } from 'node:readline';
import { existsSync } from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';

const releaseFile = process.env.AIMODELER_ACP_TEST_RELEASE_FILE;
const input = createInterface({ input: process.stdin, crlfDelay: Infinity });

function write(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

for await (const line of input) {
  if (!line.trim()) continue;
  const message = JSON.parse(line);
  const { id, method, params = {} } = message;

  if (method === 'initialize') {
    write({ jsonrpc: '2.0', id, result: { protocolVersion: 1, agentCapabilities: {} } });
  } else if (method === 'session/new') {
    write({ jsonrpc: '2.0', id, result: { sessionId: 'fixture-session', configOptions: [] } });
  } else if (method === 'session/prompt') {
    if (params.fixture_stall === true) continue;

    const sessionId = params.sessionId;
    const promptText = params.prompt?.[0]?.text ?? 'fixture';
    const responseText = releaseFile ? promptText : 'fixture';
    write({
      jsonrpc: '2.0',
      method: 'session/update',
      params: {
        sessionId,
        update: {
          sessionUpdate: 'agent_message_chunk',
          content: { type: 'text', text: responseText },
        },
      },
    });

    if (releaseFile) {
      const deadline = Date.now() + 5_000;
      while (!existsSync(releaseFile) && Date.now() < deadline) await delay(10);
      if (!existsSync(releaseFile)) {
        write({ jsonrpc: '2.0', id, error: { code: -32001, message: 'fixture release timed out' } });
        continue;
      }
    }

    for (const update of [
      { sessionUpdate: 'agent_thought_chunk', content: { type: 'text', text: 'fixture thought' } },
      { sessionUpdate: 'tool_call', toolCallId: 'fixture-tool-1', title: 'fixture tool', status: 'running' },
      { sessionUpdate: 'session_info_update', message: 'fixture status' },
    ]) {
      write({ jsonrpc: '2.0', method: 'session/update', params: { sessionId, update } });
    }
    write({ jsonrpc: '2.0', id, result: { stopReason: 'end_turn' } });
  } else if (method === 'fixture/error') {
    write({ jsonrpc: '2.0', id, error: { code: -32000, message: 'fixture error' } });
  } else if (method === 'fixture/exit') {
    process.exit(0);
  }
}
