// Package engine owns the desktop-to-CAD-host process boundary.
package engine

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os/exec"
	"sync"
	"sync/atomic"
	"time"
)

type Event struct {
	Event     string          `json:"event"`
	RequestID uint64          `json:"request_id"`
	Data      json.RawMessage `json:"data"`
}

type reply struct {
	ID        uint64          `json:"id"`
	Result    json.RawMessage `json:"result"`
	Error     json.RawMessage `json:"error"`
	Event     string          `json:"event"`
	RequestID uint64          `json:"request_id"`
	Data      json.RawMessage `json:"data"`
}

type response struct {
	value json.RawMessage
	err   error
}

type Client struct {
	command   *exec.Cmd
	input     io.WriteCloser
	writeGate chan struct{}
	mu        sync.Mutex
	pending   map[uint64]chan response
	sequence  atomic.Uint64
	closed    chan struct{}
	exited    chan struct{}
	onEvent   func(Event)
	onLog     func(string)
	closeOnce sync.Once
}

type Options struct {
	Executable, Resources, DataDir, Config string
	OnEvent                                func(Event)
	OnLog                                  func(string)
}

func Start(options Options) (*Client, error) {
	args := []string{"--resources", options.Resources, "--data", options.DataDir}
	if options.Config != "" {
		args = append(args, "--config", options.Config)
	}
	command := exec.Command(options.Executable, args...)
	return startCommand(command, options)
}

func startCommand(command *exec.Cmd, options Options) (*Client, error) {
	hideConsole(command)
	input, err := command.StdinPipe()
	if err != nil {
		return nil, err
	}
	output, err := command.StdoutPipe()
	if err != nil {
		input.Close()
		return nil, err
	}
	stderr, err := command.StderrPipe()
	if err != nil {
		input.Close()
		return nil, err
	}
	c := &Client{command: command, input: input, writeGate: make(chan struct{}, 1), pending: make(map[uint64]chan response),
		closed: make(chan struct{}), exited: make(chan struct{}), onEvent: options.OnEvent, onLog: options.OnLog}
	if err := command.Start(); err != nil {
		input.Close()
		return nil, err
	}
	stdoutDone := make(chan struct{})
	stderrDone := make(chan struct{})
	go func() { defer close(stdoutDone); c.read(output) }()
	go func() {
		defer close(stderrDone)
		scanner := bufio.NewScanner(stderr)
		scanner.Buffer(make([]byte, 4096), 1024*1024)
		for scanner.Scan() {
			if c.onLog != nil {
				c.onLog(scanner.Text())
			}
		}
		if err := scanner.Err(); err != nil {
			c.fail(fmt.Errorf("读取引擎日志失败: %w", err))
		}
	}()
	go func() {
		// Wait closes the pipes. Drain both streams before reaping the child.
		<-stdoutDone
		<-stderrDone
		err := command.Wait()
		if err == nil {
			err = errors.New("建模引擎已关闭")
		}
		c.fail(err)
		close(c.exited)
	}()
	return c, nil
}

func (c *Client) read(output io.Reader) {
	scanner := bufio.NewScanner(output)
	scanner.Buffer(make([]byte, 64*1024), 64*1024*1024)
	for scanner.Scan() {
		var message reply
		if err := json.Unmarshal(scanner.Bytes(), &message); err != nil {
			c.fail(fmt.Errorf("建模引擎协议无效: %w", err))
			return
		}
		if message.Event != "" {
			if c.onEvent != nil {
				c.onEvent(Event{Event: message.Event, RequestID: message.RequestID, Data: message.Data})
			}
			continue
		}
		c.mu.Lock()
		waiting := c.pending[message.ID]
		delete(c.pending, message.ID)
		c.mu.Unlock()
		if waiting == nil {
			continue
		}
		result := response{value: message.Result}
		if len(message.Error) > 0 && string(message.Error) != "null" {
			var description string
			if json.Unmarshal(message.Error, &description) != nil {
				description = string(message.Error)
			}
			result.err = errors.New(description)
		}
		waiting <- result
	}
	if err := scanner.Err(); err != nil {
		c.fail(err)
	} else {
		c.fail(io.EOF)
	}
}

func (c *Client) fail(err error) {
	c.closeOnce.Do(func() {
		c.mu.Lock()
		close(c.closed)
		for id, waiting := range c.pending {
			waiting <- response{err: err}
			delete(c.pending, id)
		}
		c.mu.Unlock()
		// A broken pipe or malformed stream cannot serve further requests. Stop
		// the child so both reader goroutines can finish and Wait can reap it.
		go func() {
			// Killing the reader first releases a synchronous Windows pipe write.
			// Closing that pipe directly can wait for the blocked writer's lock.
			_ = c.command.Process.Kill()
			_ = c.input.Close()
		}()
	})
}

func (c *Client) Call(ctx context.Context, method string, params any, result any) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	id := c.sequence.Add(1)
	request, err := json.Marshal(map[string]any{"id": id, "method": method, "params": params})
	if err != nil {
		return err
	}
	waiting := make(chan response, 1)
	c.mu.Lock()
	select {
	case <-c.closed:
		c.mu.Unlock()
		return errors.New("建模引擎连接已关闭")
	default:
		c.pending[id] = waiting
	}
	c.mu.Unlock()
	defer func() { c.mu.Lock(); delete(c.pending, id); c.mu.Unlock() }()
	select {
	case c.writeGate <- struct{}{}:
	case <-ctx.Done():
		return ctx.Err()
	case <-c.closed:
		return errors.New("建模引擎连接已关闭")
	}
	if err := ctx.Err(); err != nil {
		<-c.writeGate
		return err
	}
	select {
	case <-c.closed:
		<-c.writeGate
		return errors.New("建模引擎连接已关闭")
	default:
	}
	written := make(chan error, 1)
	go func() {
		_, err := c.input.Write(append(request, '\n'))
		if err != nil {
			c.fail(err)
		}
		written <- err
		<-c.writeGate
	}()
	select {
	case err := <-written:
		if err != nil {
			return err
		}
	case <-ctx.Done():
		select {
		case <-written:
			// A complete frame can still receive a reply that read will discard.
		default:
			// A partially written JSONL frame cannot be followed by another call.
			c.fail(ctx.Err())
		}
		return ctx.Err()
	case <-c.closed:
		// fail has delivered an error to every pending call. A reply that won
		// the race with shutdown remains valid and is decoded below.
	}
	select {
	case response := <-waiting:
		if response.err != nil {
			return response.err
		}
		if result == nil {
			return nil
		}
		return json.Unmarshal(response.value, result)
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (c *Client) Close() {
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
	defer cancel()
	_ = c.Call(ctx, "shutdown", map[string]any{}, nil)
	select {
	case <-c.exited:
		return
	case <-ctx.Done():
		c.fail(ctx.Err())
	}
	// Reaping normally completes immediately after Kill, but a blocked consumer
	// callback must not make application shutdown wait forever.
	select {
	case <-c.exited:
	case <-time.After(time.Second):
	}
}
