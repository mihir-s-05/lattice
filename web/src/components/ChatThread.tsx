import type { Message } from '../types';

export function ChatThread({ messages }: { messages: Message[] }) {
  return (
    <div aria-label="chat-thread">
      {messages.map((m) => (
        <div key={m.id} className="message">
          <div className="meta">
            <strong>{m.role}</strong>
          </div>
          <div className="text">{m.text}</div>
          {m.annotations && m.annotations.length > 0 && (
            <div className="annotations">
              {m.annotations.map((a) => (
                <sup key={a.ref}>[{a.ref}]</sup>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
