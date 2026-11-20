import { createInterface } from 'node:readline';

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
const write = value => process.stdout.write(`${JSON.stringify(value)}\n`);
let active;
for await (const line of input) {
  const { id, method, params = {} } = JSON.parse(line);
  if (method === 'initialize') {
    write({ jsonrpc: '2.0', id, result: { protocolVersion: 1, agentCapabilities: {} } });
  } else if (method === 'session/new') {
    write({ jsonrpc: '2.0', id, result: { sessionId: 'rpc-fixture', configOptions: [] } });
  } else if (method === 'session/prompt') {
    active = id;
    write({ jsonrpc: '2.0', method: 'session/update', params: {
      sessionId: params.sessionId,
      update: { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'Waiting for cancellation' } },
    } });
  } else if (method === 'session/cancel' && active !== undefined) {
    write({ jsonrpc: '2.0', id: active, result: { stopReason: 'cancelled' } });
    active = undefined;
  } else if (method === 'session/close') {
    write({ jsonrpc: '2.0', id, result: {} });
  }
}
