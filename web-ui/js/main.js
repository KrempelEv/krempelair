var KrempelAirApp = angular.module('KrempelAirApp', ['ngRoute']);
var uri = "/api"

KrempelAirApp.config(['$locationProvider', function($locationProvider) {
  $locationProvider.hashPrefix('');
}]);


KrempelAirApp.config(function ($routeProvider) {
  $routeProvider
      .when('/', {
        templateUrl: '../views/air.html',
        controller: 'AirFlowController'
      })
      .when('/login', {
        templateUrl: '../views/login.html',
        controller: 'LoginCtrl'
      })
      .otherwise({
        redirectTo: '/'
      });
})

KrempelAirApp.directive('selectOnClick', ['$window', function ($window) {
    return {
        restrict: 'A',
        link: function (scope, element, attrs) {
            element.on('click', function () {
                if (!$window.getSelection().toString()) {
                    // Required for mobile Safari
                    this.setSelectionRange(0, this.value.length)
                }
            });
        }
    };
}]);

KrempelAirApp.factory('Auth', function ($rootScope, $window, $http) {
  return {
    login: function (user, successHandler, errorHandler) {
      if (user.username == 'test' && user.password == 'test') {
        this.setLoggedInUser(user);
        successHandler(user);
      } else {
        alert("please use username/password as test/test to login");
        errorHandler(user);
      }

      // call the server side login restful api
//      $http.post('api/login', user).success(function(user) {
//        this.setLoggedInUser(user);
//        successHandler(user);
//      }).error(errorHandler);
    },
    getLoggedInUser: function () {
      if ($rootScope.user === undefined || $rootScope.user == null) {
        var userStr = $window.sessionStorage.getItem('user');
        if (userStr) {
          $rootScope.user = angular.fromJson(userStr);
        }
      }
      return $rootScope.user;
    },
    isLoggedIn: function () {
      return this.getLoggedInUser() != null;
    },
    setLoggedInUser: function (user) {
      $rootScope.user = user;
      if (user == null) {
        $window.sessionStorage.removeItem('user');
      } else {
        $window.sessionStorage.setItem('user', angular.toJson($rootScope.user));
      }
    }
  };
})

KrempelAirApp.run(['$window', '$rootScope', '$location', 'Auth', function ($window, $rootScope, $location, Auth) {

    $rootScope.$on("$routeChangeStart", function (event) {
      if (!Auth.isLoggedIn() &&
          $location.path() !== '/login') {
        $location.path('/login');
      }
    });
}]);

KrempelAirApp.controller('LoginCtrl', function ($scope, Auth, $location, $http) {
  $scope.user = {};

  $scope.login = function () {
    Auth.login($scope.user, function () {
      $location.path('/');
    }, function (e) {
      // do some error handling.
    });
  };
})

KrempelAirApp.controller('MainCtrl', function ($scope, Auth, $location) {

  $scope.logout = function () {
    Auth.setLoggedInUser(null);
    $location.path('/somewhere');
  };
})

KrempelAirApp.controller('AirFlowController', function AirFlowController($scope, $http, $timeout) {


    $scope.Refresh = function(){
        $http.get(uri+"/").then(function (response) {
            $scope.status = response.data;
        });
        $http.get(uri+"/stoerung").then(function (response) {
            $scope.stoerung = response.data;
        });
        $http.get(uri+"/lueftung/temperatur").then(function (response) {
            $scope.temperatur = response.data;
            if($scope.sollTemp && (parseFloat(parseFloat($scope.sollTemp).toFixed(2)) !== parseFloat(parseFloat(response.data["TempSoll"]).toFixed(2)))){
                $http.get(uri+"/lueftung/temperatur/sollTemp/"+parseFloat($scope.sollTemp));
            }else{
                $scope.sollTemp = response.data["TempSoll"];
            }
            if($scope.sollTempNAK && (parseFloat(parseFloat($scope.sollTempNAK).toFixed(2)) !== parseFloat(parseFloat(response.data["TempSollNAK"]).toFixed(2)))){
                $http.get(uri+"/lueftung/temperatur/sollTempNAK/"+parseFloat($scope.sollTempNAK));
            }else{
                $scope.sollTempNAK = response.data["TempSollNAK"];
            }
        });


        $timeout(function(){
         $scope.Refresh();
        },800);
    }
    $scope.Klick = function(Klick){
        $http.get(uri+"/lueftung/stufe/"+Klick);
        $scope.Refresh();
    }
    
    $scope.updateNAK = function(){
      if($scope.NAK){
        $http.get(uri+"/lueftung/NAK/1");
      }else{
        $http.get(uri+"/lueftung/NAK/0");
      }
    }

    $scope.Refresh();
});
