# -*- coding: utf-8 -*-
# Copyright 2016 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Google PubSub Listener for requests to Turbinia to process evidence."""

from __future__ import unicode_literals

import copy
import json
import logging
import uuid
from Queue import Queue

from google.cloud import pubsub

# Turbinia
from turbinia import config
from turbinia import evidence
from turbinia import TurbiniaException

log = logging.getLogger('turbinia')


class TurbiniaRequest(object):
  """An object to request evidence to be processed.

  Attributes:
    request_id: A client specified ID for this request.
    recipe: Recipe to use when processing this request.
    context: A Dict of context data to be passed around with this request.
    evidence: A list of Evidence objects.
  """

  def __init__(
      self, request_id=None, recipe=None, context=None, evidence_=None):
    """Initialization for TurbiniaRequest."""
    self.request_id = request_id if request_id else uuid.uuid4().hex
    self.recipe = recipe
    self.context = context if context else {}
    self.evidence = evidence_ if evidence_ else []
    self.type = self.__class__.__name__

  def to_json(self):
    """Convert object to JSON.

    Returns:
      A JSON serialized object.
    """
    serializable = copy.deepcopy(self.__dict__)
    serializable['evidence'] = [x.serialize() for x in serializable['evidence']]

    try:
      serialized = json.dumps(serializable)
    except TypeError as e:
      msg = (
          'JSON serialization of TurbiniaRequest object {0:s} failed: '
          '{1:s}'.format(self.type, str(e)))
      raise TurbiniaException(msg)

    return serialized

  def from_json(self, json_str):
    """Loads JSON serialized data into self.

    Args:
      json_str (str): Json serialized TurbiniaRequest object.

    Raises:
      TurbiniaException: If json can not be loaded, or deserialized object is
          not of the correct type.
    """
    try:
      obj = json.loads(json_str)
    except ValueError as e:
      raise TurbiniaException(
          'Can not load json from string {0:s}'.format(str(e)))

    if obj.get('type', None) != self.type:
      raise TurbiniaException(
          'Deserialized object does not have type of {0:s}'.format(self.type))

    obj['evidence'] = [evidence.evidence_decode(e) for e in obj['evidence']]
    # pylint: disable=attribute-defined-outside-init
    self.__dict__ = obj


class TurbiniaPubSub(object):
  """PubSub client object for Google Cloud.

  Attributes:
    _queue: A Queue object for storing pubsub messages
    topic_name (str): The full pubsub topic name
    topic_subpath (str): The partial name of the last
    subscriber: The pubsub subscriber client object
    publisher: The pubsub publisher client object
    subscription: The pubsub subscription object
  """

  def __init__(self, topic_subpath):
    """Initialization for PubSubClient."""
    self._queue = Queue()
    self.publisher = None
    self.subscriber = None
    self.subscription = None
    self.subscription_name = None
    self.topic_name = None
    self.topic_subpath = topic_subpath

  def setup_publisher(self):
    """Set up the pubsub publisher client."""
    config.LoadConfig()
    print "DEBUG: in setup_publisher()"
    self.publisher = pubsub.PublisherClient()
    self.topic_name = self.publisher.topic_path(
        config.PROJECT, self.topic_subpath)
    log.debug('Setup PubSub publisher {0:s}'.format(self.topic_name))

  def setup_subscriber(self):
    """Set up the pubsub subscriber client."""
    config.LoadConfig()
    self.subscriber = pubsub.SubscriberClient()
    self.subscription_name = self.subscriber.subscription_path(
        config.PROJECT, self.topic_subpath)

    log.debug('Setup PubSub Subscription {0:s}'.format(
        self.subscription_name))
    self.subscription = self.subscriber.subscribe(self.subscription_name)
    self.subscription.open(self._callback)

  def _callback(self, message):
    """Callback function that places messages in the queue.

    Args:
      message: A pubsub message object
    """
    log.debug('Recieved pubsub message: {0:s}'.format(message.data))
    message.ack()
    self._queue.put(message)

  def _validate_message(self, message):
    """Validates pubsub messages and returns them as a new TurbiniaRequest obj.

    Args:
      message: PubSub message string

    Returns:
      A TurbiniaRequest object or None if there are decoding failures.
    """
    request = TurbiniaRequest()
    try:
      request.from_json(message)
    except TurbiniaException as e:
      log.error('Error decoding pubsub message: {0:s}'.format(str(e)))
      return None

    return request

  def check_messages(self):
    """Checks for pubsub messages.

    Returns:
      A list of any TurbiniaRequest objects received, else an empty list
    """
    requests = []
    for _ in xrange(self._queue.qsize()):
      message = self._queue.get()
      data = message.data
      log.info('Processing PubSub message {0:s}'.format(message.message_id))
      log.debug('PubSub message body: {0:s}'.format(data))

      request = self._validate_message(data)
      if request:
        requests.append(request)
      else:
        log.error('Error processing PubSub message: {0:s}'.format(data))

    return requests

  def send_message(self, message):
    """Send a pubsub message.

    message: The message to send.
    """
    data = message.encode('utf-8')
    future = self.publisher.publish(self.topic_name, data)
    msg_id = future.result()
    log.info('Published message {0:s} to topic {1:s}'.format(
        msg_id, self.topic_name))

  def send_request(self, request):
    """Sends a TurbiniaRequest message.

    Args:
      request: A TurbiniaRequest object.
    """
    self.send_message(request.to_json())
