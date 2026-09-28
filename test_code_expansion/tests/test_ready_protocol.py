#!/usr/bin/env python3
"""Scripted MPI ordering/lifetime checks; does not replace test_ready_neighbors."""
import os
from pathlib import Path
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    tmp=Path(directory)
    (tmp/'mpi.h').write_text('''#pragma once
using MPI_Comm=int;using MPI_Datatype=int;using MPI_Request=int;
struct MPI_Status {int MPI_SOURCE;int MPI_ERROR;};
constexpr int MPI_SUCCESS=0,MPI_INT=1,MPI_INT64_T=2,MPI_UNDEFINED=-32766;
constexpr int MPI_REQUEST_NULL=0,MPI_MAX_ERROR_STRING=256;
constexpr int MPI_ERR_COUNT=4,MPI_ERR_OTHER=5,MPI_ERR_RANK=6;
#define MPI_STATUS_IGNORE ((MPI_Status*)0)
#define MPI_STATUSES_IGNORE ((MPI_Status*)0)
int MPI_Comm_rank(MPI_Comm,int*);double MPI_Wtime();
int MPI_Error_string(int,char*,int*);int MPI_Abort(MPI_Comm,int);
int MPI_Isend(const void*,int,MPI_Datatype,int,int,MPI_Comm,MPI_Request*);
int MPI_Irecv(void*,int,MPI_Datatype,int,int,MPI_Comm,MPI_Request*);
int MPI_Waitall(int,MPI_Request*,MPI_Status*);
int MPI_Waitany(int,MPI_Request*,int*,MPI_Status*);
''')
    binary=tmp/'test_ready_protocol'
    subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-Wall','-Wextra','-Werror',
        '-I',str(tmp),'-I',str(ROOT/'mesh_occ_mpi'),str(ROOT/'tests/test_ready_protocol.cpp'),
        '-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
