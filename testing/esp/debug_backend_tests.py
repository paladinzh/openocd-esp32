# GDB + OpenOCD tests

import time
import os.path
import logging
import unittest
import importlib
import debug_backend as dbg

# TODO: fixed???
ESP32_BLD_FLASH_OFF = 0x1000
ESP32_PT_FLASH_OFF = 0x8000
# TODO: get from partition table
ESP32_APP_FLASH_OFF = 0x10000
ESP32_APP_FLASH_SZ = (1024*1024) # 1M
# TODO: get automatically
ESP32_FLASH_SZ =  4*(1024*1024) # 4M

test_apps_dir = ''


def get_logger():
    """ Returns logger for this module
    """
    return logging.getLogger(__name__)


class DebuggerTestError(RuntimeError):
    """ Base class for debugger's test errors
    """
    pass


class DebuggerTestAppConfig:
    """ Test application config.
        Application binaries (elf and bin) are implied to be located in $test_apps_dir/$app_name/$bin_dir
    """

    def __init__(self, bin_dir='', build_dir='', src_dir='', app_name='', app_off=ESP32_APP_FLASH_OFF):
        # Base path for app binaries, relative $test_apps_dir/$app_name
        self.bin_dir = bin_dir
        # Base path for app object files, relative $test_apps_dir/$app_name
        self.build_dir = build_dir
        # App name
        self.app_name = app_name
        # App binary offeset in flash
        self.app_off = app_off
        # Path for bootloader binary, relative $test_apps_dir/$app_name/$bin_dir
        self.bld_path = None
        # App binary offeset in flash
        self.bld_off = ESP32_BLD_FLASH_OFF
        # Path for partitions table binary, relative $test_apps_dir/$app_name/$bin_dir
        self.pt_path = None
        # App binary offeset in flash
        self.pt_off = ESP32_PT_FLASH_OFF
        # name of test app variable which selects sub-test to run
        self.test_select_var = None
    
    def __repr__(self):
        return '%s/%s/%x-%s/%x-%s/%x' % (self.app_name, self.bin_dir, self.app_off, self.bld_path, self.bld_off, self.pt_path, self.pt_off)

    def build_src_dir(self):
        return os.path.join(test_apps_dir, self.app_name)

    def build_obj_dir(self):
        return os.path.join(test_apps_dir, self.app_name, self.build_dir)

    def build_bins_dir(self):
        return os.path.join(test_apps_dir, self.app_name, self.bin_dir)

    def build_bld_bin_path(self):
        return os.path.join(self.build_bins_dir(), self.bld_path)

    def build_pt_bin_path(self):
        return os.path.join(self.build_bins_dir(), self.pt_path)

    def build_app_bin_path(self):
        return os.path.join(self.build_bins_dir(), '%s.bin' % self.app_name)

    def build_app_elf_path(self):
        return os.path.join(self.build_bins_dir(), '%s.elf' % self.app_name)


class DebuggerTestsBunch(unittest.BaseTestSuite):
    """ Custom suite which supports groupping tests by target app and
        loading necessary binaries before tst group run.
    """

    def __init__(self, tests=()):
        self.load_app_bins = True
        self.modules = {}
        self._groupped_suites = {}
        super(DebuggerTestsBunch, self).__init__(tests)

    def addTest(self, test):
        """ Adds test
        """
        if type(test) is DebuggerTestsBunch:
            for t in test:
                self.addTest(t)
            return
        get_logger().debug('Add test %s', test)
        super(DebuggerTestsBunch, self).addTest(test)
        if test.__module__  not in self.modules:
            get_logger().debug('Add test module %s', test.__module__)
            self.modules[test.__module__] = importlib.import_module(test.__module__)
            # get_logger().debug('Modules: %s', self.modules)

    def run(self, result, debug=False):
        """ Runs tests
        """
        self._group_tests(self)
        for app_cfg_id in self._groupped_suites:
            # check if suite have at least one test to run 
            skip_count = 0
            for test in self._groupped_suites[app_cfg_id][1]:
                if getattr(type(test), '__unittest_skip__', False):
                    skip_count += 1
            if skip_count == self._groupped_suites[app_cfg_id][1].countTestCases():
                get_logger().debug('Skip loading %s for %d tests', app_cfg_id, skip_count)
            else:
                get_logger().debug('Load %s for %d tests', app_cfg_id, self._groupped_suites[app_cfg_id][1].countTestCases())
                # load only if app bins are configured (used) for these tests
                if self.load_app_bins and self._groupped_suites[app_cfg_id][0]:
                    self._load_app(self._groupped_suites[app_cfg_id][0])
                dbg.get_gdb().exec_file_set(self._groupped_suites[app_cfg_id][0].build_app_elf_path())
            self._groupped_suites[app_cfg_id][1]._run_tests(result, debug)
        return result

    def _run_tests(self, result, debug=False):
        """ Runs groups of tests
        """
        for test in self:
            if result.shouldStop:
                break
            get_logger().debug('<<<<<<<<< START %s >>>>>>>', test.id())
            if not debug:
                test(result)
            else:
                test.debug()
            get_logger().debug('======= END %s =======', test.id())

    def _group_tests(self, tests):
        """ Groups tests by target app
        """
        for test in tests:
            if type(test) is not DebuggerTestsBunch:
                app_cfg = getattr(test, 'test_app_cfg', False)
                if app_cfg:
                    app_cfg_id = str(app_cfg)
                else:
                    app_cfg_id = '' # test does not use app
                if app_cfg_id not in self._groupped_suites:
                    # print 'Add new suite for (%s)' % (app_name)
                    self._groupped_suites[app_cfg_id] = [app_cfg, DebuggerTestsBunch()]
                # print 'Add test %s to (%s)' % (test, app_name)
                self._groupped_suites[app_cfg_id][1].addTest(test)
            else:
                # print 'Group suite %s' % (test)
                self._group_tests(test)

    def _load_app(self, app_cfg):
        """ Loads application binaries to target.
        """
        gdb = dbg.get_gdb()
        state,rsn = gdb.get_target_state()
        # print 'DebuggerTestAppTests.LOAD_APP %s / %s' % (cls, app_bins)
        if state != dbg.Gdb.TARGET_STATE_STOPPED:
            gdb.exec_interrupt()
            gdb.wait_target_state(dbg.Gdb.TARGET_STATE_STOPPED, 5)
        # write bootloader
        gdb.target_program(app_cfg.build_bld_bin_path(), app_cfg.bld_off)
        # write partition table
        gdb.target_program(app_cfg.build_pt_bin_path(), app_cfg.pt_off)
        # write application
        # Currently we can not use GDB ELF loading facility for ESP32, so write binary image instead
        # _gdb.target_download()
        gdb.target_program(app_cfg.build_app_bin_path(), app_cfg.app_off)
        gdb.target_reset()


class DebuggerTestsBase(unittest.TestCase):
    """ Base class for all tests
    """
    def __init__(self, methodName):
        super(DebuggerTestsBase, self).__init__(methodName)
        self.gdb = dbg.get_gdb()
        self.oocd = dbg.get_oocd()

    def stop_exec(self):
        """ Stops target execution and ensures that it is in STOPPED state
        """
        state,rsn = self.gdb.get_target_state()
        if state != dbg.Gdb.TARGET_STATE_STOPPED:
            self.gdb.exec_interrupt()
            self.gdb.wait_target_state(dbg.Gdb.TARGET_STATE_STOPPED, 5)

    def resume_exec(self, loc=None):
        """ Resumes target execution and ensures that it is in RUNNING state
        """
        state,rsn = self.gdb.get_target_state()
        if state != dbg.Gdb.TARGET_STATE_RUNNING:
            if loc:
                get_logger().debug('Resume from addr 0x%x', pc)
                self.gdb.exec_jump(loc)
            else:
                self.gdb.exec_continue()
            self.gdb.wait_target_state(dbg.Gdb.TARGET_STATE_RUNNING, 5)


    def step(self):
        """ Performs program step
        """
        self.gdb.exec_next()
        self.gdb.wait_target_state(dbg.Gdb.TARGET_STATE_RUNNING, 5)
        rsn = self.gdb.wait_target_state(dbg.Gdb.TARGET_STATE_STOPPED, 5)
        self.assertEqual(rsn, dbg.Gdb.TARGET_STOP_REASON_STEPPED)

class DebuggerTestAppTests(DebuggerTestsBase):
    """ Base class for tests which need special app running on target
    """

    def __init__(self, methodName):
        super(DebuggerTestAppTests, self).__init__(methodName)
        self.test_app_cfg = DebuggerTestAppConfig()

    def setUp(self):
        """ Setup test.
            In order to select sub-test all tests of this class need target to be reset and halted.
        """
        self.stop_exec()
        self.gdb.target_reset()
        rsn = self.gdb.wait_target_state(dbg.Gdb.TARGET_STATE_STOPPED, 10)
        bp = self.gdb.add_bp('app_main')
        self.resume_exec()
        rsn = self.gdb.wait_target_state(dbg.Gdb.TARGET_STATE_STOPPED, 10)
        # workarounds for strange debugger's behaviour
        if rsn == dbg.Gdb.TARGET_STOP_REASON_SIGTRAP:
            get_logger().warning('Unexpected SIGTRAP during setup! Apply workaround...')
            cur_frame = self.gdb.get_current_frame()
            self.assertEqual(cur_frame['addr'], '0x40000450')
            self.resume_exec()
            rsn = self.gdb.wait_target_state(dbg.Gdb.TARGET_STATE_STOPPED, 10)
        elif rsn == dbg.Gdb.TARGET_STOP_REASON_SIGINT:            
            get_logger().warning('Unexpected SIGINT during setup! Apply workaround...')
            cur_frame = self.gdb.get_current_frame()
            # SIGINT address varies
            # self.assertEqual(cur_frame['addr'], '0x4000921a')
            self.resume_exec()
            rsn = self.gdb.wait_target_state(dbg.Gdb.TARGET_STATE_STOPPED, 10)
        self.assertEqual(rsn, dbg.Gdb.TARGET_STOP_REASON_BP)
        frame = self.gdb.get_current_frame()
        self.assertEqual(frame['func'], 'app_main')
        self.gdb.delete_bp(bp)
        # ready to select and start test (should be done in test method)

    def select_sub_test(self, sub_test_num):
        """ Selects sub test in app running on target
        """
        self.gdb.data_eval_expr('%s=%d' % (self.test_app_cfg.test_select_var, sub_test_num))


class DebuggerGenericTestAppTests(DebuggerTestAppTests):
    """ Base class to run tests which use generic test app
    """

    def __init__(self, methodName):
        super(DebuggerGenericTestAppTests, self).__init__(methodName)
        self.test_app_cfg.app_name = 'gen_ut_app'
        self.test_app_cfg.bld_path = os.path.join('bootloader', 'bootloader.bin')
        self.test_app_cfg.pt_path = 'partitions_singleapp.bin'
        self.test_app_cfg.test_select_var = 'run_test'


class DebuggerGenericTestAppTestsDual(DebuggerGenericTestAppTests):
    """ Base class to run tests which use generic test app in dual core mode
    """

    def __init__(self, methodName='runTest'):
        super(DebuggerGenericTestAppTestsDual, self).__init__(methodName)
        # use default config with modified path to binaries
        self.test_app_cfg.bin_dir = os.path.join('output', 'default')
        self.test_app_cfg.build_dir = os.path.join('builds', 'default')

class DebuggerGenericTestAppTestsSingle(DebuggerGenericTestAppTests):
    """ Base class to run tests which use generic test app in single core mode
    """

    def __init__(self, methodName='runTest'):
        super(DebuggerGenericTestAppTestsSingle, self).__init__(methodName)
        # use default config with modified path to binaries
        self.test_app_cfg.bin_dir = os.path.join('output', 'single_core')
        self.test_app_cfg.build_dir = os.path.join('builds', 'single_core')