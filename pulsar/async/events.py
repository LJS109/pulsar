from inspect import isgenerator

from pulsar.utils.pep import iteritems

from .defer import Deferred, async, InvalidStateError
from .access import logger


__all__ = ['EventHandler', 'Event', 'OneTime']


class AbstractEvent(object):
    '''Abstract event handler.'''
    _silenced = False

    @property
    def silenced(self):
        '''Boolean indicating if this event is silenced.

        To silence an event one uses the :meth:`silence` method.
        '''
        return self._silenced

    def bind(self, callback, errback=None):
        '''Bind a ``callback`` and an optional ``errback`` to this event.
        '''
        raise NotImplementedError

    def fired(self):
        '''The number of times this event has fired'''
        raise NotImplementedError

    def fire(self, arg, **kwargs):
        '''Fire this event.'''
        raise NotImplementedError

    def silence(self):
        '''Silence this event.

        A silenced event won't fire when the :meth:`fire` method is called.
        '''
        self._silenced = True


class Event(AbstractEvent):
    '''The default implementation of :class:`AbstractEvent`.
    '''
    def __init__(self):
        self._handlers = []
        self._fired = 0

    def __repr__(self):
        return repr(self._handlers)
    __str__ = __repr__

    def bind(self, callback, errback=None):
        if errback:
            raise ValueError('errback not supported in many-times events')
        self._handlers.append(callback)

    def fired(self):
        return self._fired

    def fire(self, arg, **kwargs):
        if not self._silenced:
            self._fired += 1
            for hnd in self._handlers:
                try:
                    g = hnd(arg, **kwargs)
                except Exception:
                    logger().exception('Exception while firing event')
                else:
                    if isgenerator(g):
                        # Add it to the event loop
                        async(g)


class OneTime(Deferred, AbstractEvent):
    '''An :class:`AbstractEvent` which can be fired once only.

    This event handler is a :class:`.Deferred`.

    Implemented mainly for the one time events of the :class:`EventHandler`.
    There shouldn't be any reason to use this class on its own.
    '''
    def __init__(self):
        super(OneTime, self).__init__()
        self._events = Deferred()

    def bind(self, callback, errback=None):
        self._events.add_callback(callback, errback)

    def fired(self):
        return int(self._events.done())

    def fire(self, arg, **kwargs):
        if not self._silenced:
            if kwargs:
                raise ValueError(("One time events don't support "
                                  "key-value parameters"))
            else:
                result = self._events.callback(arg)
                if isinstance(result, Deferred):
                    # a deferred, add a check at the end of the callback pile
                    return self._events.add_callback(self._check, self._check)
                elif not self._chained_to:
                    return self.callback(result)

    def _check(self, result):
        if self._events.has_callbacks:
            # other callbacks have been added,
            # put another check at the end of the pile
            return self._events.add_callback(self._check, self._check)
        elif not self._chained_to:
            return self.callback(result)


class EventHandler(object):
    '''A Mixin for handling events.

    It handles :class:`OneTime` events and :class:`Event` that occur
    several times.
    '''
    ONE_TIME_EVENTS = ()
    '''Event names which occur once only.'''
    MANY_TIMES_EVENTS = ()
    '''Event names which occur several times.'''
    def __init__(self, one_time_events=None, many_times_events=None):
        one = self.ONE_TIME_EVENTS
        if one_time_events:
            one = set(one)
            one.update(one_time_events)
        events = dict(((name, OneTime()) for name in one))
        many = self.MANY_TIMES_EVENTS
        if many_times_events:
            many = set(many)
            many.update(many_times_events)
        events.update(((name, Event()) for name in many))
        self._events = events

    @property
    def events(self):
        '''The dictionary of all events.
        '''
        return self._events

    def event(self, name):
        '''Returns the :class:`Event` at ``name``.

        If no event is registered for ``name`` returns nothing.
        '''
        return self._events.get(name)

    def bind_event(self, name, callback, errback=None):
        '''Register a ``callback`` with ``event``.

        **The callback must be a callable accepting one parameter only**,
        the instance firing the event or the first positional argument
        passed to the :meth:`fire_event` method.

        :param name: the event name. If the event is not available a warning
            message is logged.
        :param callback: a callable receiving one positional parameter. It
            can also be a list/tuple of callables.
        :return: nothing.
        '''
        if name not in self._events:
            self._events[name] = Event()
        event = self._events[name]
        if isinstance(callback, (list, tuple)):
            assert errback is None, "list of callbacks with errback"
            for cbk in callback:
                event.bind(cbk)
        else:
            event.bind(callback, errback)

    def bind_events(self, **events):
        '''Register all known events found in ``events`` key-valued parameters.
        '''
        for name in self._events:
            if name in events:
                self.bind_event(name, events[name])

    def fire_event(self, name, arg=None, **kwargs):
        """Dispatches ``arg`` or ``self`` to event ``name`` listeners.

        * If event at ``name`` is a one-time event, it makes sure that it was
          not fired before.

        :param arg: optional argument passed as positional parameter to the
            event handler.
        :param kwargs: optional key-valued parameters to pass to the event
            handler. Can only be used for
            :ref:`many times events <many-times-event>`.
        :return: for one-time events, it returns whatever is returned by the
            event handler. For many times events it returns nothing.
        """
        if arg is None:
            arg = self
        if name in self._events:
            try:
                return self._events[name].fire(arg, **kwargs)
            except InvalidStateError:
                logger().error('Event %s already fired' % name)
        else:
            logger().warning('Unknown event "%s" for %s', name, self)

    def silence_event(self, name):
        '''Silence event ``name``.

        This causes the event not to fire at the :meth:`fire_event` method
        is invoked with the event ``name``.
        '''
        event = self._events.get(name)
        if event:
            event.silence()

    def chain_event(self, other, name):
        '''Chain the event ``name`` from ``other``.

        :param other: an :class:`EventHandler` to chain to.
        :param name: event name to chain.
        '''
        event = self._events.get(name)
        if event and isinstance(other, EventHandler):
            event2 = other._events.get(name)
            if event2:
                event.chain(event2)

    def copy_many_times_events(self, other):
        '''Copy :ref:`many times events <many-times-event>` from  ``other``.

        All many times events of ``other`` are copied to this handler
        provided the events handlers already exist.
        '''
        if isinstance(other, EventHandler):
            events = self._events
            for name, event in iteritems(other._events):
                if isinstance(event, Event):
                    ev = events.get(name)
                    # If the event is available add it
                    if ev:
                        for callback in event._handlers:
                            ev.bind(callback)
