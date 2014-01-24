'''Replicate asyncio basic functionalities.
For documentation check the python 3.4 standard library
'''
import logging
import traceback
import weakref
from inspect import isgenerator

from pulsar.utils.pep import default_timer

from .coro import coroutine_return
from .tracelogger import _TracebackLogger

fallback = True


class _StopError(BaseException):
    """Raised to stop the event loop."""


def _raise_stop_error(*args):
    raise _StopError


class AbstractEventLoopPolicy(object):
    """Abstract policy for accessing the event loop."""

    def get_event_loop(self):
        """XXX"""
        raise NotImplementedError

    def set_event_loop(self, event_loop):
        """XXX"""
        raise NotImplementedError

    def new_event_loop(self):
        """XXX"""
        raise NotImplementedError


# Event loop policy.  The policy itself is always global, even if the
# policy's rules say that there is an event loop per thread (or other
# notion of context).  The default policy is installed by the first
# call to get_event_loop_policy().
_event_loop_policy = None


def get_event_loop_policy():
    """XXX"""
    global _event_loop_policy
    return _event_loop_policy


def set_event_loop_policy(policy):
    """XXX"""
    global _event_loop_policy
    assert policy is None or isinstance(policy, AbstractEventLoopPolicy)
    _event_loop_policy = policy


def get_event_loop():
    return get_event_loop_policy().get_event_loop()


def set_event_loop(event_loop):
    """XXX"""
    get_event_loop_policy().set_event_loop(event_loop)


def new_event_loop(**kwargs):
    """XXX"""
    return get_event_loop_policy().new_event_loop(**kwargs)


class AbstractServer(object):
    """Abstract server returned by create_server()."""

    def close(self):
        """Stop serving.

        This leaves existing connections open."""
        return NotImplemented

    def wait_closed(self):
        """Wait until service is closed."""
        return NotImplemented


class AbstractEventLoop(object):
    '''This is just a signature'''

    def run_in_executor(self, executor, callback, *args):
        raise NotImplementedError

    def run_until_complete(self, future):
        raise NotImplementedError

    def sock_connect(self, sock, address):
        raise NotImplementedError

    def sock_accept(self, sock):
        raise NotImplementedError


class Handle(object):
    _cancelled = False

    def __init__(self, callback, args):
        self._callback = callback
        self._args = args

    def __repr__(self):
        return '%s: %s' % (self.__class__.__name__, self._callback)
    __str__ = __repr__

    def cancel(self):
        '''Attempt to cancel the callback.'''
        self._cancelled = True


class TimerHandle(Handle):

    def __init__(self, when, callback, args):
        self._when = when
        self._callback = callback
        self._args = args

    def __lt__(self, other):
        return self._when < other._when


class BaseEventLoop(AbstractEventLoop):
    _default_executor = None

    def run_until_complete(self, future):
        future = tasks.async(future, loop=self)
        future.add_done_callback(_raise_stop_error)
        self.run_forever()
        future.remove_done_callback(_raise_stop_error)
        if not future.done():
            raise RuntimeError('Event loop stopped before Future completed.')
        return future.result()

    def stop(self):
        self.call_soon(_raise_stop_error)

    def time(self):
        return default_timer()

    def call_later(self, delay, callback, *args):
        return self.call_at(self.time() + delay, callback, *args)

    def call_soon(self, callback, *args):
        handle = TimerHandle(None, callback, args)
        self._ready.append(handle)
        return handle

    #################################################    THREAD INTERACTION
    def call_soon_threadsafe(self, callback, *args):
        handle = self.call_soon(callback, *args)
        self._write_to_self()
        return handle

    def set_default_executor(self, executor):
        self._default_executor = executor

    def _write_to_self(self):
        raise NotImplementedError


###########################################################################
##  ABSTRACT TRANSPORT

class BaseTransport(object):

    def __init__(self, extra=None):
        if extra is None:
            extra = {}
        self._extra = extra

    def get_extra_info(self, name, default=None):
        """Get optional transport information."""
        return self._extra.get(name, default)

    def close(self):
        """Closes the transport.

        Buffered data will be flushed asynchronously.  No more data
        will be received.  After all buffered data is flushed, the
        protocol's connection_lost() method will (eventually) called
        with None as its argument.
        """
        raise NotImplementedError


class ReadTransport(BaseTransport):
    """ABC for read-only transports."""

    def pause_reading(self):
        raise NotImplementedError

    def resume_reading(self):
        raise NotImplementedError


class WriteTransport(BaseTransport):
    """ABC for write-only transports."""

    def set_write_buffer_limits(self, high=None, low=None):
        raise NotImplementedError

    def get_write_buffer_size(self):
        """Return the current size of the write buffer."""
        raise NotImplementedError

    def write(self, data):
        """Write some data bytes to the transport.

        This does not block; it buffers the data and arranges for it
        to be sent out asynchronously.
        """
        raise NotImplementedError

    def writelines(self, list_of_data):
        """Write a list (or any iterable) of data bytes to the transport.

        The default implementation just calls write() for each item in
        the list/iterable.
        """
        for data in list_of_data:
            self.write(data)

    def write_eof(self):
        """Closes the write end after flushing buffered data.

        (This is like typing ^D into a UNIX program reading from stdin.)

        Data may still be received.
        """
        raise NotImplementedError

    def can_write_eof(self):
        """Return True if this protocol supports write_eof(), False if not."""
        raise NotImplementedError

    def abort(self):
        """Closes the transport immediately.

        Buffered data will be lost.  No more data will be received.
        The protocol's connection_lost() method will (eventually) be
        called with None as its argument.
        """
        raise NotImplementedError


class Transport(ReadTransport, WriteTransport):
    """ABC representing a bidirectional transport."""


class DatagramTransport(BaseTransport):
    """ABC for datagram (UDP) transports."""

    def sendto(self, data, addr=None):
        """Send data to the transport.

        This does not block; it buffers the data and arranges for it
        to be sent out asynchronously.
        addr is target socket address.
        If addr is None use target address pointed on transport creation.
        """
        raise NotImplementedError

    def abort(self):
        """Closes the transport immediately.

        Buffered data will be lost.  No more data will be received.
        The protocol's connection_lost() method will (eventually) be
        called with None as its argument.
        """
        raise NotImplementedError


class BaseProtocol:
    """ABC for base protocol class."""

    def connection_made(self, transport):
        pass

    def connection_lost(self, exc):
        pass

    def pause_writing(self):
        pass

    def resume_writing(self):
        pass


class Protocol(BaseProtocol):
    """ABC representing a protocol."""

    def data_received(self, data):
        """Called when some data is received.

        The argument is a bytes object.
        """

    def eof_received(self):
        """Called when the other end calls write_eof() or equivalent.

        If this returns a false value (including None), the transport
        will close itself.  If it returns a true value, closing the
        transport is up to the protocol.
        """


class DatagramProtocol(BaseProtocol):
    """ABC representing a datagram protocol."""

    def datagram_received(self, data, addr):
        """Called when some datagram is received."""

    def connection_refused(self, exc):
        """Connection is refused."""


###############################################################################
##    FUTURE


class Error(Exception):
    '''Raised when no other information is available on a Failure'''


class CancelledError(Error):
    pass


class TimeoutError(Error):
    pass


class InvalidStateError(Error):
    """The operation is not allowed in this state."""


_PENDING = 'PENDING'
_CANCELLED = 'CANCELLED'
_FINISHED = 'FINISHED'


class Future(object):
    # Class variables serving as defaults for instance variables.
    _state = _PENDING
    _result = None
    _exception = None
    _loop = None

    _blocking = False  # proper use of future (yield vs yield from)

    _tb_logger = None        # Used for Python 3.3 only

    def __init__(self, loop=None):
        if loop is None:
            self._loop = get_event_loop()
        else:
            self._loop = loop
        self._callbacks = []

    def __repr__(self):
        res = self.__class__.__name__
        if self._state == _FINISHED:
            if self._exception is not None:
                res += '<exception={!r}>'.format(self._exception)
            else:
                res += '<result={!r}>'.format(self._result)
        elif self._callbacks:
            size = len(self._callbacks)
            if size > 2:
                res += '<{}, [{}, <{} more>, {}]>'.format(
                    self._state, self._callbacks[0],
                    size-2, self._callbacks[-1])
            else:
                res += '<{}, {}>'.format(self._state, self._callbacks)
        else:
            res += '<{}>'.format(self._state)
        return res

    def cancel(self):
        """Cancel the future and schedule callbacks.

        If the future is already done or cancelled, return False.  Otherwise,
        change the future's state to cancelled, schedule the callbacks and
        return True.
        """
        if self._state != _PENDING:
            return False
        self._state = _CANCELLED
        self._schedule_callbacks()
        return True

    def _schedule_callbacks(self):
        """Internal: Ask the event loop to call all callbacks.

        The callbacks are scheduled to be called as soon as possible. Also
        clears the callback list.
        """
        callbacks = self._callbacks[:]
        if not callbacks:
            return

        self._callbacks[:] = []
        for callback in callbacks:
            self._loop.call_soon(callback, self)

    def cancelled(self):
        """Return True if the future was cancelled."""
        return self._state == _CANCELLED

    # Don't implement running(); see http://bugs.python.org/issue18699

    def done(self):
        """Return True if the future is done.

        Done means either that a result / exception are available, or that the
        future was cancelled.
        """
        return self._state != _PENDING

    def result(self):
        """Return the result this future represents.

        If the future has been cancelled, raises CancelledError.  If the
        future's result isn't yet available, raises InvalidStateError.  If
        the future is done and has an exception set, this exception is raised.
        """
        if self._state == _CANCELLED:
            raise CancelledError
        if self._state != _FINISHED:
            raise InvalidStateError('Result is not ready.')
        if self._tb_logger is not None:
            self._tb_logger.clear()
            self._tb_logger = None
        if self._exception is not None:
            raise self._exception
        return self._result

    def exception(self):
        """Return the exception that was set on this future.

        The exception (or None if no exception was set) is returned only if
        the future is done.  If the future has been cancelled, raises
        CancelledError.  If the future isn't done yet, raises
        InvalidStateError.
        """
        if self._state == _CANCELLED:
            raise CancelledError
        if self._state != _FINISHED:
            raise InvalidStateError('Exception is not set.')
        if self._tb_logger is not None:
            self._tb_logger.clear()
            self._tb_logger = None
        return self._exception

    def add_done_callback(self, fn):
        """Add a callback to be run when the future becomes done.

        The callback is called with a single argument - the future object. If
        the future is already done when this is called, the callback is
        scheduled with call_soon.
        """
        if self._state != _PENDING:
            self._loop.call_soon(fn, self)
        else:
            self._callbacks.append(fn)

    # New method not in PEP 3148.

    def remove_done_callback(self, fn):
        """Remove all instances of a callback from the "call when done" list.

        Returns the number of callbacks removed.
        """
        filtered_callbacks = [f for f in self._callbacks if f != fn]
        removed_count = len(self._callbacks) - len(filtered_callbacks)
        if removed_count:
            self._callbacks[:] = filtered_callbacks
        return removed_count

    # So-called internal methods (note: no set_running_or_notify_cancel()).

    def set_result(self, result):
        """Mark the future done and set its result.

        If the future is already done when this method is called, raises
        InvalidStateError.
        """
        if self._state != _PENDING:
            raise InvalidStateError('{}: {!r}'.format(self._state, self))
        self._result = result
        self._state = _FINISHED
        self._schedule_callbacks()

    def set_exception(self, exception):
        """Mark the future done and set an exception.

        If the future is already done when this method is called, raises
        InvalidStateError.
        """
        if self._state != _PENDING:
            raise InvalidStateError('{}: {!r}'.format(self._state, self))
        self._exception = exception
        self._state = _FINISHED
        self._schedule_callbacks()
        self._tb_logger = _TracebackLogger(exception)
        # Arrange for the logger to be activated after all callbacks
        # have had a chance to call result() or exception().
        self._loop.call_soon(self._tb_logger.activate)

    # Truly internal methods.

    def _copy_state(self, other):
        """Internal helper to copy state from another Future.

        The other Future may be a concurrent.futures.Future.
        """
        assert other.done()
        if self.cancelled():
            return
        assert not self.done()
        if other.cancelled():
            self.cancel()
        else:
            exception = other.exception()
            if exception is not None:
                self.set_exception(exception)
            else:
                result = other.result()
                self.set_result(result)

    def __iter__(self):
        if not self.done():
            self._blocking = True
            yield self  # This tells Task to wait for completion.
        coroutine_return(self.result()) # May raise too.


def sleep(delay, result=None, loop=None):
    future = Future(loop=loop)
    future._loop.call_later(delay, future.set_result, result)
    return future


iscoroutine = isgenerator


class Task(Future):
    _all_tasks = weakref.WeakSet()
    _current_tasks = {}

    @classmethod
    def current_task(cls, loop=None):
        if loop is None:
            loop = get_event_loop()
        return cls._current_tasks.get(loop)

    @classmethod
    def all_tasks(cls, loop=None):
        if loop is None:
            loop = get_event_loop()
        return {t for t in cls._all_tasks if t._loop is loop}

    def __init__(self, coro, loop=None):
        assert iscoroutine(coro), repr(coro)  # Not a coroutine function!
        super(Task, self).__init__(loop=loop)
        self._coro = iter(coro)  # Use the iterator just in case.
        self._fut_waiter = None
        self._must_cancel = False
        self._loop.call_soon(self._step)
        self.__class__._all_tasks.add(self)

    def __repr__(self):
        res = super().__repr__()
        if (self._must_cancel and
            self._state == _PENDING and
            '<PENDING' in res):
            res = res.replace('<PENDING', '<CANCELLING', 1)
        i = res.find('<')
        if i < 0:
            i = len(res)
        res = res[:i] + '(<{}>)'.format(self._coro.__name__) + res[i:]
        return res

    def get_stack(self, limit=None):
        frames = []
        f = self._coro.gi_frame
        if f is not None:
            while f is not None:
                if limit is not None:
                    if limit <= 0:
                        break
                    limit -= 1
                frames.append(f)
                f = f.f_back
            frames.reverse()
        elif self._exception is not None:
            tb = self._exception.__traceback__
            while tb is not None:
                if limit is not None:
                    if limit <= 0:
                        break
                    limit -= 1
                frames.append(tb.tb_frame)
                tb = tb.tb_next
        return frames

    def print_stack(self, limit=None, file=None):
        extracted_list = []
        checked = set()
        for f in self.get_stack(limit=limit):
            lineno = f.f_lineno
            co = f.f_code
            filename = co.co_filename
            name = co.co_name
            if filename not in checked:
                checked.add(filename)
                linecache.checkcache(filename)
            line = linecache.getline(filename, lineno, f.f_globals)
            extracted_list.append((filename, lineno, name, line))
        exc = self._exception
        if not extracted_list:
            print('No stack for %r' % self)
        elif exc is not None:
            print('Traceback for %r (most recent call last):' % self)
        else:
            print('Stack for %r (most recent call last):' % self)
        traceback.print_list(extracted_list, file=file)
        if exc is not None:
            for line in traceback.format_exception_only(exc.__class__, exc):
                print(line)

    def cancel(self):
        if self.done():
            return False
        if self._fut_waiter is not None:
            if self._fut_waiter.cancel():
                # Leave self._fut_waiter; it may be a Task that
                # catches and ignores the cancellation so we may have
                # to cancel it again later.
                return True
        # It must be the case that self._step is already scheduled.
        self._must_cancel = True
        return True

    def _wakeup(self, future):
        try:
            value = future.result()
        except Exception as exc:
            # This may also be a cancellation.
            self._step(None, exc)
        else:
            self._step(value, None)
        self = None  # Needed to break cycles when an exception occurs.
