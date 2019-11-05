#!/usr/bin/python3 -u

import argparse
import liblo
import os
import signal
import sys
import time
import subprocess

from PyQt5.QtCore import (QCoreApplication, QTimer, pyqtSignal,
                          QObject, QProcess, QSettings)

import ray
from multi_daemon_file import MultiDaemonFile

server_operations = ('quit', 'change_root', 'list_session_templates', 
    'list_user_client_templates', 'list_factory_client_templates', 
    'remove_client_template', 'list_sessions', 'new_session',
    'open_session')

session_operations = ('save', 'save_as_template', 'take_snapshot',
                      'close', 'abort', 'duplicate', 'open_snapshot',
                      'rename', 'add_executable', 'add_proxy',
                      'add_client_template', 'list_snapshots')

def signalHandler(sig, frame):
    if sig in (signal.SIGINT, signal.SIGTERM):
        QCoreApplication.quit()

class Signaler(QObject):
    done = pyqtSignal(int)
    daemon_started = pyqtSignal()
    daemon_no_announce = pyqtSignal()


class OscServerThread(liblo.ServerThread):
    def __init__(self):
        liblo.ServerThread.__init__(self)
        self.m_daemon_address = None
    
    @liblo.make_method('/reply', None)
    def replyNone(self, path, args, types, src_addr):
        if len(args) >= 1:
            reply_path = args[0]
        else:
            return
        
        if reply_path == '/ray/server/list_sessions':
            if len(args) >= 2:
                sessions = args[1:]
                out_message = ""
                for session in sessions:
                    out_message += "%s\n" % session
                sys.stdout.write(out_message)
                return
            else:
                signaler.done.emit(0)
        elif reply_path in ('/ray/server/list_factory_client_templates',
                            '/ray/server/list_user_client_templates'):
            if len(args) >= 2:
                templates = args[1:]
                out_message = ""
                for template_and_icon in templates:
                    template, slash, icon = template_and_icon.partition('/')
                    out_message += "%s\n" % template
                sys.stdout.write(out_message)
                return
            else:
                signaler.done.emit(0)
        elif reply_path == '/ray/session/list_snapshots':
            if len(args) >= 2:
                snapshots = args[1:]
                out_message = ""
                for snapshot_and_info in snapshots:
                    snapshot, slash, info = snapshot_and_info.partition(':')
                    out_message += "%s\n" % snapshot
                sys.stdout.write(out_message)
                return
            else:
                signaler.done.emit(0)
            
        elif len(args) == 2:
            reply_path, message = args
            sys.stdout.write("%s\n" % message)
            signaler.done.emit(0)
        #elif len(args) == 3:
            #reply_path, err, message = args
            #sys.stdout.write("%s\n" % message)
            
            #signaler.done.emit(- err)
    
    @liblo.make_method('/error', 'sis')
    def errorMessage(self, path, args, types, src_addr):
        error_path, err, message = args
        sys.stdout.write('%s\n' % message)
        
        signaler.done.emit(- err)
        
    @liblo.make_method('/ray/control/server/announce', 'siisi')
    def rayControlServerAnnounce(self, path, args, types, src_addr):
        self.m_daemon_address = src_addr
        signaler.daemon_started.emit()
        
    def setDaemonAddress(self, daemon_port):
        self.m_daemon_address = liblo.Address(daemon_port)
    
    def toDaemon(self, *args):
        self.send(self.m_daemon_address, *args)

def printHelp(stdout=False):
    message="""control RaySession daemons
opions are
    new_session NEW_SESSION_NAME [SESSION_TEMPLATE]
    open_session SESSION_NAME [SESSION_TEMPLATE]
    list_sessions
    quit
        Aborts current session (if any) and stop the daemon
    change_root
        Changes root directory for the sessions
    list_session_templates
    list_user_client_templates
    list_factory_client_templates
    remove_client_template CLIENT_TEMPLATE
    
    save
        Saves the current session
    save_as_template SESSION_TEMPLATE_NAME
        Saves the current session as template
    take_snapshot SNAPSHOT_NAME
        Takes a snapshot of the current session
    close
        Saves and Closes the current session
    abort
        Aborts current session
    duplicate NEW_SESSION_NAME
        Saves, duplicates the current session and load the new one
    open_snapshot SNAPSHOT
        Saves, close the session, back to SNAPSHOT and re-open it
    rename NEW_SESSION_NAME
        renames the current session to NEW_SESSION_NAME
    add_executable EXECUTABLE
        Adds a client to the current session
    add_proxy
        Adds a proxy client to the current session
    add_client_template CLIENT_TEMPLATE
        Adds a client to the current session from CLIENT_TEMPLATE
    list_snapshots
        Lists all snapshots of the current session
"""
    if stdout:
        sys.stdout.write(message)
    else:
        sys.stderr.write(message)
        
def finished(err_code):
    global exit_code, exit_initiated
    if not exit_initiated:
        exit_initiated = True
        exit_code = err_code
        QCoreApplication.quit()

def daemonStarted():
    global daemon_announced
    daemon_announced = True
    osc_message = '/ray/'
    if operation in server_operations:
        osc_message += 'server/'
    elif operation in session_operations:
        osc_message += 'session/'
    osc_message += operation
    print(osc_message, *arg_list)
    osc_server.toDaemon(osc_message, *arg_list)
        
    #if operation == 'list':
        #osc_server.toDaemon('/ray/server/list_sessions', 0)
    #else:
        #osc_server.toDaemon('/nsm/server/%s' % operation, *arg_list)

def daemonNoAnnounce():
    if daemon_announced:
        return
    
    sys.stderr.write("daemon didn't announce and will be killed\n")
    sys.exit(1)

def getDefaultPort():
    daemon_list = multi_daemon_file.getDaemonList()
    
    for daemon in daemon_list:
        if (daemon.user == os.environ['USER']
                and not daemon.not_default):
            return daemon.port
    return 0

if __name__ == '__main__':
    ray.addSelfBinToPath()
    
    if len(sys.argv) <= 1:
        printHelp()
        sys.exit(100)
    
    operation = sys.argv[1]
    #if not operation in ('add', 'save', 'open', 'new', 'duplicate', 
                         #'close', 'abort', 'quit', 'list'):
        #printHelp()
        #sys.exit(100)
    
    arg_list = []
    if len(sys.argv) >= 3:
        for arg in sys.argv[2:]:
            if arg.isdigit():
                arg_list.append(int(arg))
            elif arg.replace('.', '', 1).isdigit():
                arg_list.append(float(arg))
            else:
                arg_list.append(arg)
    
    #if operation in ('add', 'open', 'new', 'duplicate'):
        #if not arg_list:
            #sys.stderr.write('missing argument after "%s"\n' % operation)
            #sys.exit(100)
    
    exit_code = 0
    exit_initiated = False
    daemon_announced = False
    
    multi_daemon_file = MultiDaemonFile(None, None)
    daemon_list = multi_daemon_file.getDaemonList()
    
    app = QCoreApplication(sys.argv)
    app.setApplicationName(ray.APP_TITLE)
    app.setOrganizationName(ray.APP_TITLE)
    settings = QSettings()
    
    signaler = Signaler()
    signaler.done.connect(finished)
    signaler.daemon_started.connect(daemonStarted)
    
    osc_server = OscServerThread()
    osc_server.start()
    daemon_port = getDefaultPort()
    
    if daemon_port:
        if not (operation in server_operations
                or operation in session_operations):
            printHelp()
            sys.exit(1)
        
        osc_server.setDaemonAddress(daemon_port)
        signaler.daemon_started.emit()
    else:
        if not operation in server_operations:
            if operation in session_operations:
                sys.stderr.write("No server started. So no session to %s\n"
                                 % operation)
            else:
                printHelp()
            sys.exit(1)
        
        if operation == 'quit':
            sys.stderr.write('No server to quit !\n')
            sys.exit(0)
        
        session_root = settings.value('default_session_root',
                                      ray.DEFAULT_SESSION_ROOT)
        
        # start a daemon because no one is running
        # fake to be a gui to get daemon announce
        #daemon_process = subprocess.Popen(
            #['ray-daemon', '--control-url', str(osc_server.url),
             #'--session-root', session_root])
        daemon_process = subprocess.Popen(
            ['ray-daemon', '--control-url', str(osc_server.url),
             '--session-root', session_root],
            -1, None, None, subprocess.DEVNULL, subprocess.DEVNULL)
        QTimer.singleShot(2000, daemonNoAnnounce)
    
    #connect SIGINT and SIGTERM
    signal.signal(signal.SIGINT,  signalHandler)
    signal.signal(signal.SIGTERM, signalHandler)
    
    #needed for SIGINT and SIGTERM
    timer = QTimer()
    timer.setInterval(200)
    timer.timeout.connect(lambda: None)
    timer.start()
    
    ##time.sleep(0.201)
    #if nsm_port:
        #osc_server.toDaemon('/nsm/server/%s' % operation, *arg_list)
    
    app.exec()
    #osc_server.stop()
    del osc_server
    del app
    
    sys.exit(exit_code)