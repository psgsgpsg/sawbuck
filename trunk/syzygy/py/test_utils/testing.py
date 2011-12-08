# Copyright 2011 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Defines a collection of classes for running unit-tests."""
import build_project
import datetime
import logging
import optparse
import os
import presubmit
import subprocess
import sys


class Error(Exception):
  """An error class used for reporting problems while running tests."""
  pass


def BuildProjectConfig(*args, **kwargs):
  """Wraps build_project.BuildProjectConfig, but ensures that if it throws
  an error it is of type testing.Error."""
  try:
    build_project.BuildProjectConfig(*args, **kwargs)
  except build_project.Error:
    # Convert the exception to an instance of testing.Error, but preserve
    # the original message and stack-trace.
    raise Error, sys.exc_info()[1], sys.exc_info()[2]


class Test(object):
  """A base class embodying the notion of a test. A test has a name, and is
  invokable by calling 'Run' with a given configuration. Upon success, the
  test will create a success file in the appropriate configuration
  directory.

  The 'Main' routine of any Test object may also be called to have it
  run as as stand-alone command line test."""

  def __init__(self, project_dir, name):
    self._project_dir = project_dir
    self._name = name
    self._force = False

  def GetSuccessFilePath(self, configuration):
    """Returns the path to the success file associated with this test."""
    build_path = os.path.join(self._project_dir, '../build')
    success_path = presubmit.GetTestSuccessPath(build_path,
                                                configuration,
                                                self._name)
    return success_path

  def LastRunTime(self, configuration):
    """Returns the time this test was last run in the given configuration.
    Returns 0 if the test has no success file (equivalent to never having
    been run)."""
    try:
      return os.stat(self.GetSuccessFilePath(configuration)).st_mtime
    except (IOError, WindowsError):
      return 0

  def _CanRun(self, configuration):
    """Derived classes may override this in order to indicate that they
    should not be run in certain configurations. This stub always returns
    True."""
    return True

  def _NeedToRun(self, configuration):
    """Derived classes may override this if they can determine ahead of time
    whether the given test needs to be run. This stub always returns True."""
    return True

  def _MakeSuccessFile(self, configuration):
    """Makes the success file corresponding to this test in the given
    configuration."""
    success_path = self.GetSuccessFilePath(configuration)
    logging.info('Creating success file "%s".',
                 os.path.relpath(success_path, self._project_dir))
    success_file = open(success_path, 'wb')
    success_file.write(str(datetime.datetime.now()))
    success_file.close()

  def _Run(self, configuration):
    """This is as a stub of the functionality that must be implemented by
    child classes."""
    raise Error('_Run not overridden.')

  def Run(self, configuration, force=False):
    """Runs the test in the given configuration. The derived instance of Test
    must implement '_Run(self, configuration)', which raises an exception on
    error or does nothing on success. Upon success of _Run, this will generate
    the appropriate success file. If the test fails, the exception is left
    to propagate.

    Optional args:
      force: If True, this will force the test to re-run even if _NeedToRun
          would return False.
    """
    # Store optional arguments in a side-channel, so as to allow additions
    # without changing the _Run/_NeedToRun/_CanRun API.
    self._force = force

    if not self._CanRun(configuration):
      logging.info('Skipping test "%s" in invalid configuration "%s".',
                   self._name, configuration)
      return

    # Always run _NeedToRun, even if force is true. This is because it may
    # do some setup work that is required prior to calling _Run.
    logging.info('Checking to see if we need to run test "%s" in '
                 'configuration "%s".', self._name, configuration)
    need_to_run = self._NeedToRun(configuration)
    if need_to_run:
      logging.info('Running test "%s" in configuration "%s".',
                   self._name, configuration)
    else:
      logging.info('No need to re-run test "%s" in configuration "%s".',
                   self._name, configuration)

    if not need_to_run and force:
      logging.info('Forcing re-run of test "%s" in configuration "%s".',
                   self._name, configuration)
      need_to_run = True

    if need_to_run:
      self._Run(configuration)

    self._MakeSuccessFile(configuration)

  @staticmethod
  def _GetOptParser():
    """Builds an option parser for this class. This function is static as
    it may be called by the constructor of derived classes before the object
    is fully initialized. It may also be overridden by derived classes so that
    they may augment the option parser with additional options."""
    parser = optparse.OptionParser()
    parser.add_option('-c', '--config', dest='configs',
                      action='append', default=[],
                      help='The configuration in which you wish to run '
                           'this test. This option may be invoked multiple '
                           'times. If not specified, defaults to '
                           '["Debug", "Release"].')
    parser.add_option('-f', '--force', dest='force',
                      action='store_true', default=False,
                      help='Force tests to re-run even if not necessary.')
    parser.add_option('--verbose', dest='log_level', action='store_const',
                      const=logging.INFO, default=logging.WARNING,
                      help='Run the script with verbose logging.')
    return parser

  def Main(self):
    opt_parser = self._GetOptParser()
    options, unused_args = opt_parser.parse_args()

    logging.basicConfig(level=options.log_level)

    # If no configurations are specified, run all configurations.
    if not options.configs:
      options.configs = ['Debug', 'Release']

    result = 0
    for config in set(options.configs):
      try:
        self.Run(config, force=options.force)
      except Error:
        logging.exception('Configuration "%s" of test "%s" failed.',
            config, self._name)
        # This is deliberately a catch-all clause. We wish for each
        # configuration run to be completely independent.
        result = 1

    return result


class ExecutableTest(Test):
  """An executable test is a Test that is run by launching a single
  executable file, and inspecting its return value."""

  def __init__(self, project_dir, name):
    Test.__init__(self, project_dir, name)

  def _GetTestPath(self, configuration):
    """Returns the path to the test executable. This stub may be overridden,
    but it defaults to 'project_dir/../build/configuration/test_name.exe'."""
    return os.path.join(self._project_dir, '../build',
        configuration, '%s.exe' % self._name)

  def _NeedToRun(self, configuration):
    test_path = self._GetTestPath(configuration)
    return os.stat(test_path).st_mtime > self.LastRunTime(configuration)

  def _Run(self, configuration):
    test_path = self._GetTestPath(configuration)
    rel_test_path = os.path.relpath(test_path, self._project_dir)
    command = [test_path]
    result = subprocess.call(command)
    if result:
      raise Error('test "%s" returned with code %d' % (rel_test_path, result))


class TestSuite(Test):
  """A test suite is a collection of tests that generates a catch-all
  success file upon successful completion. It is itself an instance of a
  Test, so may be nested."""

  def __init__(self, project_dir, name, tests):
    Test.__init__(self, project_dir, name)
    # tests may be anything iterable, but we want it to be a list when
    # stored internally.
    self._tests = list(tests)

  def AddTest(self, test):
    self._tests.append(test)

  def AddTests(self, tests):
    self._tests.extend(self, tests)

  def _NeedToRun(self, configuration):
    """Determines if any of the tests in this suite need to run in the given
    configuration."""
    for test in self._tests:
      try:
        if test._NeedToRun(configuration):
          return True
      except:
        # Output some context before letting the exception continue.
        logging.error('Configuration "%s" of test "%s" failed.',
            configuration, test._name)
        raise
    return False

  def _Run(self, configuration):
    """Implementation of this Test object. Runs the provided collection of
    tests, generating a global success file upon completion of them all.
    If any test fails, raises an exception."""
    for test in self._tests:
      try:
        # If we're being forced, force our children as well!
        test.Run(configuration, force=self._force)
      except:
        # Output some context before letting the exception continue.
        logging.error('Configuration "%s" of test "%s" failed.',
            configuration, test._name)
        raise
