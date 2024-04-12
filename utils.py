"""
this file helps me debug the most common problem i've had, which is
bursts of quick requests being mishandled. i'm not sure if this should
stay here, now that i've found the problem, but i don't really care
enough to remove it :)
"""
import os
import signal

NUM_SIG = 10
PROCESS = 27184
FILE_PATH = "./pid"


def write_current_pid_in_file():
    with open(FILE_PATH, "w") as f:
        f.write(str(os.getpid()))


def getcurrentpid():
    with open(FILE_PATH, "r") as f:
        return f.readline()


if __name__ == '__main__':
    print("firing awayy")
    for i in range(NUM_SIG):
        os.kill(int(getcurrentpid()), signal.SIGUSR1)
