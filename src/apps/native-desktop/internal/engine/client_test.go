package engine

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"sync"
	"testing"
	"time"
)

type helperRequest struct {
	ID     uint64          `json:"id"`
	Method string          `json:"method"`
	Params json.RawMessage `json:"params"`
}

// The test executable acts as a real child process so pipe ownership, EOF,
// process reaping, and Windows creation flags use the production implementation.
func TestEngineHelperProcess(t *testing.T) {
	if os.Getenv("FORMRECALL_ENGINE_HELPER") != "1" {
		return
	}
	encoder := json.NewEncoder(os.Stdout)
	send := func(value any) {
		if err := encoder.Encode(value); err != nil {
			os.Exit(3)
		}
	}
	answer := func(id uint64, result any) {
		send(map[string]any{"id": id, "result": result})
	}
	fmt.Fprintln(os.Stderr, "helper started")
	var batch []helperRequest
	var deferred uint64
	mutations := 0
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 4096), 8*1024*1024)
	for scanner.Scan() {
		var request helperRequest
		if err := json.Unmarshal(scanner.Bytes(), &request); err != nil {
			os.Exit(4)
		}
		switch request.Method {
		case "echo":
			answer(request.ID, request.Params)
		case "batch":
			batch = append(batch, request)
			if len(batch) == 12 {
				for index := len(batch) - 1; index >= 0; index-- {
					answer(batch[index].ID, batch[index].Params)
				}
				batch = nil
			}
		case "error":
			send(map[string]any{"id": request.ID, "error": "模型参数无效"})
		case "object_error":
			send(map[string]any{"id": request.ID, "error": map[string]any{"code": "invalid"}})
		case "event":
			send(map[string]any{"event": "agent", "request_id": request.ID, "data": map[string]any{"stage": "mesh"}})
			answer(request.ID, map[string]bool{"done": true})
		case "defer":
			deferred = request.ID
			fmt.Fprintln(os.Stderr, "request deferred")
		case "flush":
			answer(deferred, "late")
			answer(request.ID, "current")
		case "mutate":
			mutations++
			answer(request.ID, mutations)
		case "count":
			answer(request.ID, mutations)
		case "stop_reading":
			answer(request.ID, true)
			time.Sleep(30 * time.Second)
			os.Exit(0)
		case "pause_reading":
			answer(request.ID, true)
			time.Sleep(500 * time.Millisecond)
		case "exit":
			os.Exit(7)
		case "malformed":
			fmt.Fprintln(os.Stdout, "this is not JSON")
			time.Sleep(30 * time.Second)
		case "oversized_log":
			fmt.Fprintln(os.Stderr, strings.Repeat("x", 1024*1024+100))
			time.Sleep(30 * time.Second)
		case "shutdown":
			if os.Getenv("FORMRECALL_ENGINE_IGNORE_SHUTDOWN") == "1" {
				time.Sleep(30 * time.Second)
			}
			answer(request.ID, map[string]bool{"closed": true})
			fmt.Fprintln(os.Stderr, "helper stopped")
			os.Exit(0)
		default:
			os.Exit(5)
		}
	}
	os.Exit(0)
}

func startHelper(t *testing.T, options Options, ignoreShutdown bool) *Client {
	t.Helper()
	command := exec.Command(os.Args[0], "-test.run=^TestEngineHelperProcess$")
	command.Env = append(os.Environ(), "FORMRECALL_ENGINE_HELPER=1")
	if ignoreShutdown {
		command.Env = append(command.Env, "FORMRECALL_ENGINE_IGNORE_SHUTDOWN=1")
	}
	client, err := startCommand(command, options)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = client.command.Process.Kill()
		client.Close()
	})
	return client
}

func callContext(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	t.Cleanup(cancel)
	return ctx
}

func awaitExit(t *testing.T, client *Client, timeout time.Duration) {
	t.Helper()
	select {
	case <-client.exited:
	case <-time.After(timeout):
		t.Fatal("engine child was not reaped")
	}
	client.mu.Lock()
	defer client.mu.Unlock()
	if len(client.pending) != 0 {
		t.Fatalf("pending requests remain after exit: %d", len(client.pending))
	}
}

func TestConcurrentResponsesFollowRequestIDs(t *testing.T) {
	client := startHelper(t, Options{}, false)
	ctx := callContext(t)
	var workers sync.WaitGroup
	for value := 0; value < 12; value++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			var result int
			if err := client.Call(ctx, "batch", value, &result); err != nil {
				t.Errorf("request %d: %v", value, err)
			} else if result != value {
				t.Errorf("request %d received response %d", value, result)
			}
		}()
	}
	workers.Wait()
}

func TestErrorsDoNotPoisonFollowingRequests(t *testing.T) {
	client := startHelper(t, Options{}, false)
	for method, expected := range map[string]string{"error": "模型参数无效", "object_error": `{"code":"invalid"}`} {
		err := client.Call(callContext(t), method, map[string]any{}, nil)
		if err == nil || err.Error() != expected {
			t.Fatalf("%s error = %v, want %s", method, err, expected)
		}
	}
	var result string
	if err := client.Call(callContext(t), "echo", "healthy", &result); err != nil || result != "healthy" {
		t.Fatalf("following request = %q, %v", result, err)
	}
}

func TestEventsAndLogsRemainSeparateFromReplies(t *testing.T) {
	events := make(chan Event, 1)
	logs := make(chan string, 4)
	client := startHelper(t, Options{
		OnEvent: func(event Event) { events <- event },
		OnLog:   func(line string) { logs <- line },
	}, false)
	var result struct{ Done bool }
	if err := client.Call(callContext(t), "event", map[string]any{}, &result); err != nil || !result.Done {
		t.Fatalf("event request = %+v, %v", result, err)
	}
	select {
	case event := <-events:
		if event.Event != "agent" || event.RequestID != 1 || string(event.Data) != `{"stage":"mesh"}` {
			t.Fatalf("unexpected event %+v", event)
		}
	case <-time.After(time.Second):
		t.Fatal("event was not delivered")
	}
	client.Close()
	awaitExit(t, client, time.Second)
	for _, expected := range []string{"helper started", "helper stopped"} {
		select {
		case line := <-logs:
			if line != expected {
				t.Fatalf("stderr line = %q, want %q", line, expected)
			}
		case <-time.After(time.Second):
			t.Fatalf("stderr line %q was lost", expected)
		}
	}
}

func TestTimedOutRequestIgnoresLateReply(t *testing.T) {
	client := startHelper(t, Options{}, false)
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	if err := client.Call(ctx, "defer", map[string]any{}, nil); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("timeout error = %v", err)
	}
	var result string
	if err := client.Call(callContext(t), "flush", map[string]any{}, &result); err != nil || result != "current" {
		t.Fatalf("request after late reply = %q, %v", result, err)
	}
}

func TestAlreadyCancelledRequestDoesNotReachEngine(t *testing.T) {
	client := startHelper(t, Options{}, false)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := client.Call(ctx, "mutate", map[string]any{}, nil); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled call error = %v", err)
	}
	var count int
	if err := client.Call(callContext(t), "count", map[string]any{}, &count); err != nil || count != 0 {
		t.Fatalf("cancelled request changed engine state: count=%d, error=%v", count, err)
	}
}

func TestUnexpectedExitFailsPendingRequests(t *testing.T) {
	client := startHelper(t, Options{}, false)
	err := client.Call(callContext(t), "exit", map[string]any{}, nil)
	if err == nil || errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("child exit error = %v", err)
	}
	awaitExit(t, client, time.Second)
	if err := client.Call(callContext(t), "echo", "later", nil); err == nil {
		t.Fatal("request accepted after child exit")
	}
}

func TestInvalidProtocolReapsChildWithoutExplicitClose(t *testing.T) {
	client := startHelper(t, Options{}, false)
	err := client.Call(callContext(t), "malformed", map[string]any{}, nil)
	if err == nil || !strings.Contains(err.Error(), "协议无效") {
		t.Fatalf("protocol error = %v", err)
	}
	awaitExit(t, client, time.Second)
}

func TestOversizedLogFailsTransportAndReapsChild(t *testing.T) {
	client := startHelper(t, Options{}, false)
	err := client.Call(callContext(t), "oversized_log", map[string]any{}, nil)
	if err == nil || errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("oversized log error = %v", err)
	}
	awaitExit(t, client, time.Second)
}

func TestCloseKillsUnresponsiveChild(t *testing.T) {
	client := startHelper(t, Options{}, true)
	started := time.Now()
	client.Close()
	awaitExit(t, client, time.Second)
	if elapsed := time.Since(started); elapsed > 6*time.Second {
		t.Fatalf("close exceeded shutdown deadline: %v", elapsed)
	}
	client.Close()
}

func TestConcurrentCloseReleasesPendingRequest(t *testing.T) {
	deferred := make(chan struct{}, 1)
	client := startHelper(t, Options{OnLog: func(line string) {
		if line == "request deferred" {
			deferred <- struct{}{}
		}
	}}, false)
	completed := make(chan error, 1)
	ctx := callContext(t)
	go func() { completed <- client.Call(ctx, "defer", map[string]any{}, nil) }()
	select {
	case <-deferred:
	case <-time.After(time.Second):
		t.Fatal("pending request did not reach child")
	}
	var closers sync.WaitGroup
	for range 4 {
		closers.Add(1)
		go func() { defer closers.Done(); client.Close() }()
	}
	closers.Wait()
	awaitExit(t, client, time.Second)
	select {
	case err := <-completed:
		if err == nil || errors.Is(err, context.DeadlineExceeded) {
			t.Fatalf("pending call was not released by Close: %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("pending call still blocked after Close")
	}
}

func TestCancelledBlockedWriteReapsChild(t *testing.T) {
	client := startHelper(t, Options{}, false)
	if err := client.Call(callContext(t), "stop_reading", nil, nil); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	completed := make(chan error, 1)
	go func() { completed <- client.Call(ctx, "echo", strings.Repeat("x", 4*1024*1024), nil) }()
	select {
	case err := <-completed:
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatalf("blocked write cancellation = %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("request deadline did not release a blocked pipe write")
	}
	awaitExit(t, client, time.Second)
}

func awaitWriter(t *testing.T, client *Client) {
	t.Helper()
	deadline := time.Now().Add(time.Second)
	for len(client.writeGate) == 0 {
		if time.Now().After(deadline) {
			t.Fatal("request did not acquire the pipe writer")
		}
		time.Sleep(time.Millisecond)
	}
}

func TestCancelledWaitingWriterLeavesTransportUsable(t *testing.T) {
	client := startHelper(t, Options{}, false)
	if err := client.Call(callContext(t), "pause_reading", nil, nil); err != nil {
		t.Fatal(err)
	}
	completed := make(chan error, 1)
	go func() {
		completed <- client.Call(context.Background(), "echo", strings.Repeat("x", 4*1024*1024), nil)
	}()
	awaitWriter(t, client)
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	started := time.Now()
	if err := client.Call(ctx, "mutate", nil, nil); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("queued write cancellation = %v", err)
	}
	if elapsed := time.Since(started); elapsed > 300*time.Millisecond {
		t.Fatalf("queued cancellation waited for the active writer: %v", elapsed)
	}
	select {
	case err := <-completed:
		if err != nil {
			t.Fatalf("queued cancellation broke active write: %v", err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("active writer did not recover after child resumed reading")
	}
	var count int
	if err := client.Call(callContext(t), "count", nil, &count); err != nil || count != 0 {
		t.Fatalf("cancelled queued write changed engine state: count=%d, error=%v", count, err)
	}
}

func TestCloseReleasesBlockedWriterWithoutDeadline(t *testing.T) {
	client := startHelper(t, Options{}, false)
	if err := client.Call(callContext(t), "stop_reading", nil, nil); err != nil {
		t.Fatal(err)
	}
	completed := make(chan error, 1)
	go func() {
		completed <- client.Call(context.Background(), "echo", strings.Repeat("x", 4*1024*1024), nil)
	}()
	awaitWriter(t, client)
	closed := make(chan struct{})
	go func() { client.Close(); close(closed) }()
	select {
	case <-closed:
	case <-time.After(6 * time.Second):
		t.Fatal("Close waited indefinitely for a blocked pipe write")
	}
	awaitExit(t, client, time.Second)
	select {
	case err := <-completed:
		if err == nil {
			t.Fatal("blocked request succeeded after child was killed")
		}
	case <-time.After(time.Second):
		t.Fatal("Close did not release the request without a deadline")
	}
}
